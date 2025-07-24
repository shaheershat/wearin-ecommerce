from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.views.decorators.http import require_POST, require_GET
from django.http import JsonResponse
from django.urls import reverse
from django.http import HttpResponseBadRequest, Http404
from django.db.models import Q
from django.conf import settings
from django.contrib.auth.backends import ModelBackend 
from django.db import models
from django.core.mail import send_mail
from django.views.decorators.http import require_GET
from core.utils import get_cart_items_data, validate_coupon
from datetime import timedelta
from core.tasks import send_order_cancelled_email 
from django.contrib.auth import login
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from core.decorators import user_login_required
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth import get_user
import json
import uuid 
from decimal import Decimal
from django.db.models import Sum
import logging

logger = logging.getLogger(__name__)
from core.models import Wallet, WalletTransaction
from core.models import OrderItem
from core.models import Cart, CartItem
from django.http import JsonResponse
from django.core.mail import send_mail
import razorpay
from django.views.decorators.csrf import csrf_exempt
from core.forms import UserLoginForm, UserRegistrationForm, ProfileForm, AddressForm, NewsletterForm
from core.models import Product, Category, Wishlist, Order, UserProfile, EmailOTP, Address, NewsletterSubscriber, NotificationSubscription, ReturnReason,ReturnItem, ReturnRequest,Offer
from core.emails import send_product_available_email, send_product_sold_email, send_product_removed_from_cart_email, send_order_confirmation_email
from core.tasks import release_expired_reservations_task
from core.utils import send_otp_email
from django.contrib.auth.hashers import make_password
from django.db import transaction, IntegrityError # IMPORTANT: Added IntegrityError
import random
from django.utils import timezone
import razorpay
from core.models import Address, Coupon 
from django.contrib.sessions.models import Session
from django.contrib.auth.models import User
from core.models import Cart, CartItem, Product  
from django.http import FileResponse, Http404
import os
from xhtml2pdf import pisa
from django.http import HttpResponse
from django.template.loader import get_template
from django.db import transaction
CART_RESERVATION_TIME_MINUTES = 5
DEFAULT_SHIPPING_CHARGE = Decimal('50.00')
MIN_PURCHASE_FOR_FREE_SHIPPING = Decimal('1000.00')

def refund_to_wallet(user, amount):
    with transaction.atomic():
        wallet = user.wallet
        wallet.balance += amount
        wallet.save()

def download_invoice_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    logo_url = request.build_absolute_uri('/static/images/logo.jpeg')

    template_path = 'user/main/invoice_pdf.html'
    context = {
        'order': order,
        'logo_url': logo_url,  
        'request': request
    }

    template = get_template(template_path)
    html = template.render(context)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="invoice_{order.custom_order_id}.pdf"'

    pisa_status = pisa.CreatePDF(html, dest=response)

    if pisa_status.err:
        return HttpResponse('We had some errors <pre>' + html + '</pre>')

    return response


@user_login_required
def view_order_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    return render(request, 'user/main/view_order.html', {'order': order})

@user_login_required
def cancel_order_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    if order.status not in ['Pending', 'Shipped']:
        messages.error(request, 'This order cannot be cancelled at its current status.')
        return redirect('my_profile')

    try:
        with transaction.atomic():
            order.status = 'Cancelled'
            order.save()

            # Get or create wallet
            wallet, created = Wallet.objects.get_or_create(user=request.user)
            
            # Refund amount to wallet
            wallet.credit(order.total_amount)

            # Add wallet transaction record
            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='refund',
                amount=order.total_amount,
                reason=f"Refund for cancelled order #{order.custom_order_id or order.id}"
            )

            # --- LOGIC FOR STOCK UPDATION AND IS_SOLD FLAG ---
            for item in order.items.all():
                if item.product:
                    item.product.stock_quantity += item.quantity
                    item.product.is_sold = False
                    item.product.save()
                    logger.info(f"Product '{item.product.name}' (ID: {item.product.id}) stock updated to {item.product.stock_quantity}, is_sold set to False due to order cancellation.")
                else:
                    logger.warning(f"Product for OrderItem {item.id} in Order {order.custom_order_id} not found during cancellation stock update.")

            logger.info(f"User cancelled order {order.custom_order_id}. Scheduling cancellation email.")
            send_order_cancelled_email.delay(order.id) 

            messages.success(request, f"Order has been cancelled and ₹{order.total_amount} has been refunded to your wallet.")
            return redirect('my_profile')

    except Exception as e:
        logger.error(f"Error cancelling order {order_id}: {e}", exc_info=True)
        messages.error(request, 'An unexpected error occurred while cancelling the order.')
        return redirect('my_profile')

@user_login_required
@require_GET
def get_order_items_for_return(request, order_id):
    """
    AJAX endpoint to fetch order items and return reasons for the return modal.
    """
    try:
        order = get_object_or_404(Order, id=order_id, user=request.user)
        if order.status != 'Delivered':
            return JsonResponse({'status': 'error', 'message': 'Only delivered orders can be returned.'}, status=400)
        eligible_items = []
        for item in order.items.all().select_related('product'):
            total_requested_qty = ReturnItem.objects.filter(order_item=item, 
                return_request__status__in=['Requested', 'Approved', 'Processing Refund']).aggregate(Sum('quantity'))['quantity__sum'] or 0
            
            if item.quantity > total_requested_qty:
                eligible_items.append({
                    'id': item.id,
                    'product_name': item.product.name if item.product else 'Deleted Product',
                    'image_url': item.product.images.first().image.url if item.product and item.product.images.first() else '',
                    'price_at_purchase': str(item.price_at_purchase),
                    'quantity': item.quantity,
                    'max_return_quantity': item.quantity - total_requested_qty, 
                })
        
        if not eligible_items:
             return JsonResponse({'status': 'error', 'message': 'No eligible items for return in this order.'}, status=400)

        reasons = list(ReturnReason.objects.values('id', 'reason_text', 'requires_custom_input'))

        return JsonResponse({
            'status': 'success',
            'order_id': order.id,
            'custom_order_id': order.custom_order_id,
            'items': eligible_items,
            'reasons': reasons,
        })

    except Order.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Order not found.'}, status=404)
    except Exception as e:
        logger.error(f"Error fetching order items for return: {e}")
        return JsonResponse({'status': 'error', 'message': 'An unexpected error occurred.'}, status=500)


@user_login_required
@require_POST
def create_return_request(request):
    """
    AJAX endpoint to create a new return request based on user selection.
    """
    try:
        data = json.loads(request.body)
        order_id = data.get('order_id')
        selected_items_data = data.get('selected_items') 
        reason_id = data.get('reason_id')
        custom_reason_text = data.get('custom_reason', '').strip()

        if not order_id or not selected_items_data or not reason_id:
            return JsonResponse({'status': 'error', 'message': 'Missing required data.'}, status=400)
        
        if not selected_items_data:
             return JsonResponse({'status': 'error', 'message': 'Please select at least one item to return.'}, status=400)


        order = get_object_or_404(Order, id=order_id, user=request.user)

        if order.status != 'Delivered':
            return JsonResponse({'status': 'error', 'message': 'Only delivered orders can be returned.'}, status=400)
        
        return_reason = get_object_or_404(ReturnReason, id=reason_id)

        if return_reason.requires_custom_input and not custom_reason_text:
            return JsonResponse({'status': 'error', 'message': 'Please provide a custom reason.'}, status=400)
        
        if not return_reason.requires_custom_input and custom_reason_text:
            custom_reason_text = ''

        with transaction.atomic():
            new_return_request = ReturnRequest.objects.create(
                user=request.user,
                order=order,
                reason=return_reason,
                custom_reason=custom_reason_text if return_reason.requires_custom_input else None,
                status='Requested'
            )

            for item_data in selected_items_data:
                item_id = item_data.get('item_id')
                return_qty = int(item_data.get('quantity', 1)) 

                order_item = get_object_or_404(OrderItem, id=item_id, order=order)

                current_returned_qty = ReturnItem.objects.filter(
                    order_item=order_item,
                    return_request__status__in=['Requested', 'Approved', 'Processing Refund']
                ).aggregate(Sum('quantity'))['quantity__sum'] or 0

                if return_qty <= 0 or (current_returned_qty + return_qty) > order_item.quantity:
                    raise ValueError(f"Invalid return quantity {return_qty} for item {order_item.product.name}. Max available for return: {order_item.quantity - current_returned_qty}.")
                
                ReturnItem.objects.create(
                    return_request=new_return_request,
                    order_item=order_item,
                    quantity=return_qty
                )
            
            messages.success(request, "Return request submitted successfully!")
            return JsonResponse({'status': 'success', 'message': 'Return request submitted successfully.'})

    except ValueError as ve:
        logger.warning(f"Validation error in create_return_request: {ve}")
        return JsonResponse({'status': 'error', 'message': str(ve)}, status=400)
    except Order.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Order not found or does not belong to you.'}, status=404)
    except ReturnReason.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Invalid return reason.'}, status=400)
    except Exception as e:
        logger.error(f"Error creating return request: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': 'An unexpected error occurred during submission.'}, status=500)

razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

@user_login_required
def mini_cart_data(request):
    cart = request.session.get('cart', {})
    items = []

    for product_id, item_data in cart.items():
        try:
            product = Product.objects.get(id=product_id)
            quantity = item_data['quantity'] if isinstance(item_data, dict) else item_data
            items.append({
                'id': product.id,
                'name': product.name,
                'image_url': product.images.first().image.url if product.images.exists() else '',
                'price': float(product.price),
                'quantity': quantity,
                'total': float(product.price) * quantity,
            })
        except Product.DoesNotExist:
            continue

    return JsonResponse({'items': items})

razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

@user_login_required
@require_POST
def remove_applied_coupon(request):
    """
    Removes the applied coupon from the user's session.
    """
    if 'applied_coupon_code' in request.session:
        del request.session['applied_coupon_code']
        messages.info(request, "Coupon has been removed.")
    return redirect('checkout')


@user_login_required
@require_GET
def user_coupon_list_view(request):
    """
    Displays a list of active and valid coupons for the user,
    with an indication if the user meets its specific criteria.
    """
    now = timezone.now()
    all_coupons = Coupon.objects.filter(
        is_active=True,
        valid_from__lte=now, 
        valid_to__gte=now   
    ).order_by('-created_at')

    coupons_with_applicability = []

    cart = None
    cart_items_queryset = CartItem.objects.none() 
    cart_total_price = Decimal('0.00')
    cart_unique_products_count = 0
    cart_total_quantity = 0

    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            cart_items_queryset = CartItem.objects.filter(
                cart=cart,
                product__is_sold=False,
                product__stock_quantity__gt=0
            )
           
            for item in cart_items_queryset:
                cart_total_price += item.product.price * item.quantity 
            cart_unique_products_count = cart_items_queryset.values('product').distinct().count()
            cart_total_quantity = cart_items_queryset.aggregate(Sum('quantity'))['quantity__sum'] or 0
        except Cart.DoesNotExist:
            pass

    for coupon in all_coupons:
        can_apply, reason = coupon.is_valid(
            cart_total_price,
            user=request.user,
            cart_items=cart_items_queryset
        )

        coupons_with_applicability.append({
            'coupon': coupon,
            'can_apply': can_apply,
            'reason': reason
        })

    context = {
        'coupons': coupons_with_applicability,
    }
    return render(request, 'user/main/coupon_list.html', context)

@user_login_required
def checkout_view(request):
    """
    Handles displaying the checkout page (GET) and processing order submission (POST).
    Applies custom offers (BOGO, Free Shipping, Fixed/Percentage per product) and coupons.
    Integrates Razorpay order creation for online payments.
    """
   
    with transaction.atomic():
        cart_data = get_cart_items_data(request)
    
    cart_items_for_order_display = cart_data['items']
    total_cart_items_original_price = cart_data['subtotal_original']
    offer_level_discount = cart_data['discount_from_offers']
    subtotal_after_offers = cart_data['subtotal_after_offers']
    user_cart_obj = cart_data['cart_obj']

    is_free_shipping = cart_data['is_free_shipping']
    if Offer.objects.filter(offer_type='FREESHIP', is_active=True, start_date__lte=timezone.now(), end_date__gte=timezone.now()).exists():
        is_free_shipping = True

    shipping_charge = Decimal('0.00') if is_free_shipping else DEFAULT_SHIPPING_CHARGE
    
    total_before_coupon = subtotal_after_offers + shipping_charge
    final_total_price = total_before_coupon
    coupon_discount_amount = Decimal('0.00')

    coupon_applied_code = request.session.get('coupon_code')
    applied_coupon_object = None

    if coupon_applied_code:
        try:
            coupon_obj = Coupon.objects.get(code=coupon_applied_code, is_active=True, valid_from__lte=timezone.now(), valid_to__gte=timezone.now())
            
            is_valid_coupon, validation_message_coupon = validate_coupon(
                coupon_obj, request.user, subtotal_after_offers, cart_items_for_order_display
            )
            
            if is_valid_coupon:
                if coupon_obj.discount_type == 'percentage':
                    coupon_discount_amount = (subtotal_after_offers * coupon_obj.discount) / Decimal('100.00')
                else:
                    coupon_discount_amount = coupon_obj.discount
                
                if coupon_discount_amount > subtotal_after_offers:
                    coupon_discount_amount = subtotal_after_offers
                
                final_total_price = total_before_coupon - coupon_discount_amount
                applied_coupon_object = coupon_obj
            else:
                messages.warning(request, f"Applied coupon '{coupon_applied_code}' is no longer valid: {validation_message_coupon}. It has been removed.")
                del request.session['coupon_code']
                coupon_applied_code = None
                applied_coupon_object = None
        except Coupon.DoesNotExist:
            messages.warning(request, "The previously applied coupon is invalid or no longer exists. It has been removed.")
            del request.session['coupon_code']
            coupon_applied_code = None
            applied_coupon_object = None
    
    final_total_price = max(Decimal('0.00'), final_total_price)

    wallet, created = Wallet.objects.get_or_create(user=request.user)
    wallet_balance = wallet.balance

    addresses = Address.objects.filter(user=request.user).order_by('-is_default')
    selected_address_id = None
    selected_address = None 

    if addresses.exists():
        default_address = addresses.filter(is_default=True).first()
        if default_address:
            selected_address = default_address
            selected_address_id = default_address.id
        else:
            selected_address = addresses.first()
            selected_address_id = addresses.first().id

    if request.method == 'POST':
        selected_address_id = request.POST.get('selected_address')
        payment_method = request.POST.get('payment_method')

        if not selected_address_id:
            messages.error(request, "Please select a shipping address.")
            return redirect('checkout')
        
        try:
            selected_address = Address.objects.get(id=selected_address_id, user=request.user)
        except Address.DoesNotExist:
            messages.error(request, "Selected address is invalid or does not belong to your account.")
            return redirect('checkout')

        if not cart_items_for_order_display:
            messages.error(request, "Your cart is empty. Please add items before proceeding.")
            return redirect('shop')

        with transaction.atomic():
            new_order = Order.objects.create(
                user=request.user,
                address=selected_address,
                total_amount=final_total_price,
                coupon_discount=coupon_discount_amount,
                shipping_charge=shipping_charge,
                payment_method=payment_method,
                status='Pending',
                payment_status='Pending'
            )

            for item_data in cart_items_for_order_display:
                product = item_data['product']
                quantity = item_data['quantity']
                
                price_at_purchase_unit = item_data['total_price'] / quantity if quantity > 0 else Decimal('0.00')

                product_db = Product.objects.select_for_update().get(id=product.id)
                if product_db.is_sold or product_db.stock_quantity < quantity:
                    transaction.set_rollback(True)
                    messages.error(request, f"Sorry, '{product_db.name}' is no longer available in the requested quantity or is sold out.")
                    return redirect('checkout')
                
                OrderItem.objects.create(
                    order=new_order,
                    product=product_db,
                    quantity=quantity,
                    price_at_purchase=price_at_purchase_unit,
                    original_total_price=item_data['original_item_total'],
                    discount_amount=item_data['discount_applied'],
                )

                product_db.stock_quantity -= quantity
                if product_db.reserved_by_user == request.user:
                    product_db.release_reservation()
                product_db.save()

            if user_cart_obj:
                user_cart_obj.items.all().delete()
            
            if request.session.get('buy_now_active'):
                del request.session['buy_now_active']
                if 'buy_now_item_id' in request.session: del request.session['buy_now_item_id']
                if 'buy_now_quantity' in request.session: del request.session['buy_now_quantity']
                request.session.modified = True

            if applied_coupon_object:
                applied_coupon_object.used_count = F('used_count') + 1
                applied_coupon_object.save(update_fields=['used_count'])
                applied_coupon_object.refresh_from_db()
                if 'coupon_code' in request.session:
                    del request.session['coupon_code']
                    request.session.modified = True

            if payment_method == 'razorpay':
                try:
                    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
                    amount_in_paisa = int(final_total_price * 100) 

                    razorpay_order = client.order.create({
                        "amount": amount_in_paisa,  
                        "currency": "INR",
                        "receipt": f"order_receipt_{new_order.id}",
                        "payment_capture": "1"
                    })
                    
                    new_order.razorpay_order_id = razorpay_order['id']
                    new_order.save() 

                    return JsonResponse({
                        'status': 'success',
                        'razorpay_order_id': razorpay_order['id'],
                        'amount': razorpay_order['amount'],
                        'currency': razorpay_order['currency'],
                        'key': settings.RAZORPAY_KEY_ID,
                        'name': 'WEARIN',
                        'description': f'Order #{new_order.id} Payment',
                        'image': '/static/images/your_logo.png',
                        'prefill': {
                            'name': request.user.get_full_name() or request.user.username,
                            'email': request.user.email,
                            'contact': selected_address.phone if selected_address.phone else '',
                        },
                        'theme': {'color': '#000000'},
                        'callback_url': request.build_absolute_uri(reverse('payment_success')),
                        'notes': {
                            'order_id': str(new_order.id),
                            'user_id': str(request.user.id),
                        }
                    })

                except razorpay.errors.BadRequestError as e:
                    messages.error(request, f"Razorpay processing error: {e}")
                    logger.error(f"Razorpay API Error: {e}", exc_info=True)
                    transaction.set_rollback(True)
                    return JsonResponse({'status': 'error', 'message': f"Payment processing error: {e}"}, status=400)
                except Exception as e:
                    messages.error(request, f"An unexpected error occurred during payment initiation: {e}")
                    logger.error(f"General Razorpay integration error: {e}", exc_info=True)
                    transaction.set_rollback(True)
                    return JsonResponse({'status': 'error', 'message': f"An unexpected error occurred during payment initiation: {e}"}, status=500)
            
            elif payment_method == 'cod':
                if final_total_price > getattr(settings, 'COD_LIMIT', Decimal('1000.00')):
                    transaction.set_rollback(True)
                    messages.error(request, f"Cash on Delivery is not available for orders above ₹{getattr(settings, 'COD_LIMIT', Decimal('1000.00'))}.")
                    return redirect('checkout')
                
                new_order.payment_method = 'COD'
                new_order.status = 'Placed'
                new_order.payment_status = 'Pending'
                new_order.save()

                messages.success(request, "Your order has been placed successfully via Cash on Delivery!")
                return redirect(reverse('order_detail', args=[new_order.id]))

            elif payment_method == 'wallet':
                if wallet.balance >= final_total_price:
                    wallet.balance -= final_total_price
                    wallet.save()
                    
                    WalletTransaction.objects.create(
                        wallet=wallet,
                        transaction_type='Debit',
                        amount=final_total_price,
                        description=f"Order payment for #{new_order.custom_order_id or new_order.id}"
                    )

                    new_order.payment_method = 'Wallet'
                    new_order.status = 'Placed'
                    new_order.payment_status = 'Paid'
                    new_order.save()

                    messages.success(request, "Your order has been placed successfully using Wallet Balance!")
                    return redirect(reverse('order_detail', args=[new_order.id]))
                else:
                    transaction.set_rollback(True)
                    messages.error(request, "Insufficient wallet balance.")
                    return redirect('checkout')

            else:
                transaction.set_rollback(True)
                messages.error(request, "Invalid payment method selected.")
                return redirect('checkout')

    context = {
        "cart_items": cart_items_for_order_display,
        "subtotal_original": total_cart_items_original_price, 
        "discount_from_offers": offer_level_discount, 
        "subtotal_after_offers": subtotal_after_offers, 
        "shipping_charge": shipping_charge,
        "is_free_shipping": is_free_shipping,
        "coupon_discount": coupon_discount_amount, 
        "total_price": final_total_price,

        "coupon_applied": coupon_applied_code,
        "applied_coupon_object": applied_coupon_object,

        "razorpay_key": settings.RAZORPAY_KEY_ID,
        "addresses": addresses,
        "selected_address_id": selected_address_id,
        "wallet_balance": wallet_balance,
        "buy_now_active": request.session.get('buy_now_active', False),
        "user_name_for_prefill": request.user.get_full_name() or request.user.username,
        "user_email_for_prefill": request.user.email,
        "user_contact_for_prefill": selected_address.phone if selected_address and selected_address.phone else '',
        
        "DEFAULT_SHIPPING_CHARGE": DEFAULT_SHIPPING_CHARGE,
        "MIN_PURCHASE_FOR_FREE_SHIPPING": MIN_PURCHASE_FOR_FREE_SHIPPING,
        "COD_LIMIT": getattr(settings, 'COD_LIMIT', Decimal('1000.00'))
    }
    return render(request, 'user/main/checkout.html', context)


@user_login_required
@transaction.atomic 
@csrf_exempt 
def payment_success_view(request):
    user = request.user
    logger.debug(f"[{user.username if user.is_authenticated else 'Anonymous'}] payment_success_view called. Method: {request.method}")

    if request.method == 'POST':
        selected_address_id = request.POST.get('address_id')
        payment_method = request.POST.get('payment_method')
        razorpay_payment_id = request.POST.get('razorpay_payment_id')
        razorpay_order_id = request.POST.get('razorpay_order_id')
        razorpay_signature = request.POST.get('razorpay_signature')

        logger.debug(f"[{user.username}] POST data - address_id: {selected_address_id}, payment_method: {payment_method}")
        logger.debug(f"[{user.username}] Razorpay details - RZ_Payment_ID: {razorpay_payment_id}, RZ_Order_ID: {razorpay_order_id}, RZ_Signature: {razorpay_signature}")


        if not selected_address_id or not payment_method:
            messages.error(request, "Missing address or payment method selection.")
            logger.warning(f"[{user.username}] Missing address or payment method in POST data.")
            return redirect('checkout')

        try:
            address = get_object_or_404(Address, id=selected_address_id, user=user)
            logger.debug(f"[{user.username}] Address found: {address.id}")
        except Http404:
            messages.error(request, "Selected address is invalid or does not belong to your account.")
            logger.error(f"[{user.username}] Invalid address ID {selected_address_id} or not owned by user.")
            return redirect('checkout')

        cart_items_data = []
        total_order_price = Decimal('0.00')

        buy_now_item_session_data = request.session.get('buy_now_item')
        logger.debug(f"[{user.username}] buy_now_item_session_data: {buy_now_item_session_data}")


        if buy_now_item_session_data:
            try:
                product = Product.objects.select_for_update().get(id=buy_now_item_session_data['id'])
                quantity = buy_now_item_session_data['quantity']

                logger.debug(f"[{user.username}] Processing buy-now product {product.id} ({product.name}).")

                if product.is_sold:
                    messages.error(request, f"'{product.name}' was sold before your payment could be processed.")
                    logger.warning(f"[{user.username}] Buy-now product {product.id} already sold.")
                    return redirect('shop')
                
                if product.is_currently_reserved:
                    if product.reserved_by_user != request.user:
                        messages.warning(request, f"'{product.name}' is currently reserved by another customer.")
                        logger.warning(f"[{user.username}] Buy-now product {product.id} reserved by another user.")
                        return redirect('product_detail', id=product.id)
                    elif product.reservation_expires_at and timezone.now() > product.reservation_expires_at:
                        messages.warning(request, f"Your reservation for '{product.name}' has expired. It is now available for others.")
                        product.release_reservation() 
                        logger.warning(f"[{user.username}] Buy-now product {product.id} reservation expired.")
                        return redirect('product_detail', id=product.id)
                
                price_at_purchase = product.price
                item_total = price_at_purchase * quantity
                
                cart_items_data.append({
                    'product': product,
                    'quantity': quantity,
                    'price_at_purchase': price_at_purchase,
                    'item_total': item_total
                })
                total_order_price += item_total
                logger.debug(f"[{user.username}] Added buy-now product {product.id} to cart_items_data. Current length: {len(cart_items_data)}. Item total: {item_total}")


            except Product.DoesNotExist:
                messages.error(request, "The product you tried to buy is no longer available.")
                logger.error(f"[{user.username}] Buy-now product {buy_now_item_session_data['id']} does not exist.")
                return redirect('shop')

        else: 
            try:
                user_cart = Cart.objects.get(user=user)
                logger.debug(f"[{user.username}] Processing DB cart with {user_cart.items.count()} items.")
                
                cart_items_to_delete_ids = []

                for item in user_cart.items.select_for_update().select_related('product'):
                    product = item.product
                    logger.debug(f"[{user.username}] Checking cart item for product {item.product.id} ({item.product.name}). is_sold: {product.is_sold}, is_reserved: {product.is_currently_reserved}")

                    if product.is_sold:
                        messages.error(request, f"'{product.name}' was sold before your payment could be processed and removed from your cart.")
                        cart_items_to_delete_ids.append(item.id)
                        logger.warning(f"[{user.username}] Cart product {product.id} already sold.")
                        continue 
                    
                    if product.is_currently_reserved:
                        if product.reserved_by_user != user:
                            messages.warning(request, f"'{product.name}' is now reserved by another user and has been removed from your cart.")
                            cart_items_to_delete_ids.append(item.id)
                            logger.warning(f"[{user.username}] Cart product {product.id} reserved by another user.")
                            continue 
                        elif product.reservation_expires_at and timezone.now() > product.reservation_expires_at:
                            messages.warning(request, f"Your reservation for '{product.name}' has expired and it was removed from your cart.")
                            product.release_reservation() 
                            cart_items_to_delete_ids.append(item.id)
                            logger.warning(f"[{user.username}] Cart product {product.id} reservation expired.")
                            continue 
                    else:
                        product.reserved_by_user = user
                        product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES) # Re-reserve for checkout duration
                        product.is_reserved = True
                        product.save()
                        logger.debug(f"[{user.username}] Cart product {product.id} reserved for current user during checkout.")

                    item_total = product.price * item.quantity
                    cart_items_data.append({
                        'product': product,
                        'quantity': item.quantity,
                        'price_at_purchase': product.price, 
                        'item_total': item_total
                    })
                    total_order_price += item_total
                    logger.debug(f"[{user.username}] Added cart product {product.id} to cart_items_data. Current length: {len(cart_items_data)}. Item total: {item_total}")
                
                if cart_items_to_delete_ids:
                    CartItem.objects.filter(id__in=cart_items_to_delete_ids).delete()
                    logger.debug(f"[{user.username}] Deleted {len(cart_items_to_delete_ids)} invalid cart items.")

            except Cart.DoesNotExist:
                messages.error(request, "Your cart is empty. No order placed.")
                logger.warning(f"[{user.username}] User cart does not exist for order placement.")
                return redirect('shop')
            except Exception as e:
                logger.error(f"[{user.username}] Error processing cart items: {e}", exc_info=True)
                messages.error(request, "An error occurred while processing your cart. Please try again.")
                return redirect('checkout')


        # --- Critical Checkpoint ---
        logger.debug(f"[{user.username}] Final cart_items_data length before zero check: {len(cart_items_data)}")
        logger.debug(f"[{user.username}] Final total_order_price before zero check: {total_order_price}")

        if total_order_price <= 0 or not cart_items_data:
            messages.error(request, "Cannot place an order with zero total price or no valid items.")
            logger.error(f"[{user.username}] Order failed: total_order_price={total_order_price}, cart_items_data empty={not bool(cart_items_data)}")
            if buy_now_item_session_data and 'buy_now_item' in request.session:
                del request.session['buy_now_item']
                request.session.modified = True
            return redirect('shop') 

        # --- Payment Verification / Debit ---
        final_payment_status = 'Pending' 
        initial_order_status = 'Pending'

        if payment_method == 'razorpay':
            if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
                messages.error(request, "Missing Razorpay payment details.")
                logger.warning(f"[{user.username}] Missing Razorpay payment details.")
                return redirect('checkout')
            try:
                params_dict = {
                    'razorpay_order_id': razorpay_order_id,
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_signature': razorpay_signature
                }
                
                razorpay_client.utility.verify_payment_signature(params_dict)
                final_payment_status = 'Success'
                initial_order_status = 'Processing' 
                logger.info(f"[{user.username}] Razorpay payment {razorpay_payment_id} verified successfully.")
            except Exception as e:
                messages.error(request, "Razorpay payment verification failed. Please try again or contact support.")
                logger.error(f"[{user.username}] Razorpay verification error for RZ_order_id {razorpay_order_id}: {e}", exc_info=True)
                raise e 

        elif payment_method == 'wallet':
            wallet, created = Wallet.objects.get_or_create(user=user)
            logger.debug(f"[{user.username}] Wallet balance: {wallet.balance}, attempting to debit: {total_order_price}")
            if wallet.balance >= total_order_price:
                if wallet.debit(total_order_price):
                    final_payment_status = 'Success'
                    initial_order_status = 'Processing' 
                    logger.info(f"[{user.username}] Wallet payment successful. New balance: {wallet.balance}")
                else:
                    messages.error(request, "Failed to debit from wallet due to an unknown error.")
                    logger.error(f"[{user.username}] Wallet debit failed unexpectedly.")
                    return redirect('checkout')
            else:
                messages.error(request, "Insufficient wallet balance to complete the order.")
                logger.warning(f"[{user.username}] Insufficient wallet balance for order. Balance: {wallet.balance}, Needed: {total_order_price}")
                return redirect('checkout')

        elif payment_method == 'cod':
            if total_order_price > 1000: 
                 messages.error(request, "Cash on Delivery (COD) not available for orders above ₹1000.")
                 logger.warning(f"[{user.username}] COD rejected for order total {total_order_price}.")
                 return redirect('checkout')
            final_payment_status = 'Pending' 
            initial_order_status = 'Pending' 
            logger.info(f"[{user.username}] COD payment method selected. Order will be Pending.")

        else:
            messages.error(request, "Invalid payment method selected.")
            logger.error(f"[{user.username}] Invalid payment method: {payment_method}")
            return redirect('checkout')

        # --- Create the Order and OrderItems ---
        order = None
        try:
            order = Order.objects.create(
                user=user,
                address=address,
                total_price=total_order_price,
                payment_method=payment_method,
                payment_status=final_payment_status,
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=razorpay_signature,
                status=initial_order_status
            )
            logger.info(f"[{user.username}] Order {order.custom_order_id} created successfully with status '{initial_order_status}' and payment status '{final_payment_status}'.")

            for item_data in cart_items_data:
                product = item_data['product']
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=item_data['quantity'],
                    price_at_purchase=item_data['price_at_purchase']
                )
                logger.debug(f"[{user.username}] OrderItem created for Product {product.id} (Qty: {item_data['quantity']}).")
                
                product.is_sold = True
                product.reserved_by_user = None
                product.reservation_expires_at = None
                product.is_reserved = False
                product.save()
                logger.debug(f"[{user.username}] Product {product.id} marked as sold and reservation cleared.")

                
            if not buy_now_item_session_data: 
                if 'user_cart' in locals() and user_cart: 
                    CartItem.objects.filter(cart=user_cart).delete() 
                    logger.debug(f"[{user.username}] All items cleared from user's cart.")
            else: 
                if 'buy_now_item' in request.session:
                    del request.session['buy_now_item']
                    request.session.modified = True
                    logger.debug(f"[{user.username}] 'buy_now_item' cleared from session.")
                
        except IntegrityError as e:
            logger.error(f"[{user.username}] IntegrityError during order creation for user {user.id}: {e}", exc_info=True)
            messages.error(request, "An error occurred while finalizing your order (database integrity). Please try again.")
            raise e 
        except Exception as e:
            logger.error(f"[{user.username}] Unhandled exception during order finalization for user {user.id}: {e}", exc_info=True)
            messages.error(request, "An unexpected error occurred during order finalization. Please contact support.")
            raise e 


        messages.success(request, f"Order #{order.custom_order_id} placed successfully!")
        
        # Send order confirmation email 
        if order:
            try:
                send_order_confirmation_email.delay(order.id)
                logger.info(f"[{user.username}] Queued order confirmation email for order {order.custom_order_id}.")
            except Exception as e:
                logger.error(f"[{user.username}] Failed to queue order confirmation email for order {order.custom_order_id}: {e}", exc_info=True)

        request.session['last_processed_order_id'] = order.id if order else None 
        request.session.modified = True
        logger.debug(f"[{user.username}] Stored order ID {order.id if order else 'None'} in session for redirect.")

        return redirect('payment_success')

    elif request.method == 'GET':
        logger.debug(f"[{user.username}] payment_success_view GET started.")
        order = None
        success_message = None
        current_messages = list(messages.get_messages(request))
        for message in current_messages:
            if message.level == messages.SUCCESS:
                success_message = str(message)
                break 

        last_processed_order_id = request.session.get('last_processed_order_id')
        logger.debug(f"[{user.username}] Retrieved last_processed_order_id from session: {last_processed_order_id}")

        if last_processed_order_id and request.user.is_authenticated:
            try:
                order = Order.objects.get(id=last_processed_order_id, user=request.user)
                logger.info(f"[{user.username}] Successfully fetched order {order.custom_order_id} for display.")
                if 'last_processed_order_id' in request.session:
                    del request.session['last_processed_order_id']
                    request.session.modified = True
                    logger.debug(f"[{user.username}] last_processed_order_id cleared from session.")
            except Order.DoesNotExist:
                messages.warning(request, "Could not retrieve specific order details, but payment was processed.")
                logger.warning(f"[{user.username}] Order {last_processed_order_id} not found for display.")
            except Exception as e:
                logger.error(f"[{user.username}] Error retrieving order {last_processed_order_id} for user {request.user.id}: {e}", exc_info=True)
                messages.error(request, "An error occurred while displaying order details.")
        elif not request.user.is_authenticated:
            messages.error(request, "You need to be logged in to view payment success details.")
            logger.warning(f"[{user.username}] Anonymous user tried to access payment success page via GET.")
            return redirect('login')

        context = {
            'order': order,
            'message': success_message if success_message else "Your payment was successful!",
            'show_invoice_button': True if order else False,
        }
        return render(request, 'user/main/payment_success.html', context)

    else:
        logger.warning(f"[{user.username}] payment_success_view received unsupported method: {request.method}")
        return HttpResponseBadRequest("Invalid request method.")

@csrf_exempt
def payment_failed_view(request):
    return render(request, 'user/main/payment_failed.html')

User = get_user_model()

# ------------------- OTP Registration Views -------------------
def request_register_otp_view(request):
    if request.method == "POST":
        email = request.POST.get("email")
        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already registered.")
        else:
            otp = str(random.randint(100000, 999999))
            EmailOTP.objects.filter(email=email, purpose="register").delete()
            EmailOTP.objects.create(email=email, otp=otp, purpose="register", created_at=timezone.now())
            send_otp_email(email, otp)
            request.session['otp_email'] = email
            return redirect('verify_register_otp')
    return render(request, 'user/auth/request_register_otp.html')

def verify_register_otp_view(request):
    email = request.session.get('otp_email')
    if not email:
        return redirect('register')

    if request.method == 'POST':
        entered_otp = request.POST.get('otp')

        try:
            otp_obj = EmailOTP.objects.get(email=email, otp=entered_otp, purpose="register")
            user_data = request.session.get('pending_user_data')

            if not user_data:
                messages.error(request, "Session expired. Please register again.")
                return redirect('register')

            form = UserRegistrationForm(user_data)
            if form.is_valid():
                user = form.save()
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')

                request.session.pop('pending_user_data', None)
                request.session.pop('otp_email', None)

                messages.success(request, "Registration successful!")
                return redirect('user_dashboard')
            else:
                messages.error(request, "Invalid session data.")
                return redirect('register')

        except EmailOTP.DoesNotExist:
            messages.error(request, "Invalid OTP. Please try again.")

    return render(request, 'user/auth/verify_register_otp.html', {'email': email})



# ------------------- OTP Forgot Password Views -------------------
def request_reset_otp_view(request):
    if request.method == "POST":
        email = request.POST.get("email")
        if not User.objects.filter(email=email).exists():
            messages.error(request, "No account found with this email.")
        else:
            otp = str(random.randint(100000, 999999))
            EmailOTP.objects.update_or_create(
                email=email,
                defaults={"otp": otp, "purpose": "reset", "created_at": timezone.now()}
            )
            send_otp_email(email, otp)
            request.session['reset_email'] = email
            request.session.save()
            return redirect('verify_reset_otp')
    return render(request, 'user/auth/request_reset_otp.html')

def verify_reset_otp_view(request):
    email = request.session.get('reset_email')
    if not email:
        return redirect('forgot_password')

    if request.method == 'POST':
        entered_otp = request.POST.get('otp')
        try:
            otp_obj = EmailOTP.objects.get(email=email, otp=entered_otp, purpose="reset")
            request.session['otp_verified'] = True
            return redirect('reset_password')
        except EmailOTP.DoesNotExist:
            messages.error(request, "Invalid OTP. Please try again.")
    return render(request, 'user/auth/verify_reset_otp.html', {"email": email})

def reset_password_view(request):
    email = request.session.get('reset_email')
    otp_verified = request.session.get('otp_verified', False)

    if not email or not otp_verified:
        return redirect('forgot_password')

    if request.method == 'POST':
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
        else:
            try:
                user = User.objects.get(email=email)
                user.set_password(password)
                user.save()

                request.session.pop('reset_email', None)
                request.session.pop('otp_verified', None)

                messages.success(request, "Password reset successful. Please login.")
                return redirect('login')
            except User.DoesNotExist:
                messages.error(request, "User not found.")

    return render(request, 'user/auth/reset_password.html', {"email": email})

# ------------------- Profile & Address Management -------------------

@user_login_required
def my_profile(request):
    profile_form = ProfileForm(instance=request.user)
    user_addresses = Address.objects.filter(user=request.user).order_by('-is_default', 'id')
    add_address_form = AddressForm()
    orders = Order.objects.filter(user=request.user).order_by('-created_at').prefetch_related('items')

    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    wallet_balance = wallet.balance

    wallet_transactions = WalletTransaction.objects.filter(wallet=wallet).order_by('-created_at')[:10]

    return render(request, 'user/main/profile.html', {
        'profile_form': profile_form,
        'user_addresses': user_addresses,
        'add_address_form': add_address_form,
        'orders': orders,
        'wallet_balance': wallet_balance,
        'wallet_transactions': wallet_transactions,
    })

@user_login_required
@require_POST
def update_profile(request):
    profile_form = ProfileForm(request.POST, instance=request.user)

    if profile_form.is_valid():
        profile_form.save()
        return JsonResponse({'success': True, 'message': 'Profile updated successfully!'})
    else:
        errors = profile_form.errors.as_dict()
        message = 'Please correct the profile errors.'
        if profile_form.non_field_errors():
            message += ' ' + ' '.join(form.non_field_errors())
        return JsonResponse({'success': False, 'errors': errors, 'message': message}, status=400)


@user_login_required
def add_address_form_view(request):
    form = AddressForm()
    return render(request, 'user/main/add_address_form.html', {'form': form})

@user_login_required
@require_POST
def add_address(request):
    if request.user.addresses.count() >= 3:
        return JsonResponse({'success': False, 'message': 'You can add a maximum of 3 addresses.'}, status=400)

    form = AddressForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            address = form.save(commit=False)
            address.user = request.user

            if address.is_default or not request.user.addresses.exists():
                request.user.addresses.update(is_default=False)
                address.is_default = True
            address.save()
            return JsonResponse({'success': True, 'message': 'Address added successfully!', 'redirect_url': reverse('checkout')})
    else:
        errors = form.errors.as_dict()
        message = 'Please correct the errors below.'
        if form.non_field_errors():
            message += ' ' + ' '.join(form.non_field_errors())
        return JsonResponse({'success': False, 'errors': errors, 'message': message}, status=400)


@user_login_required
@require_POST
def edit_address(request, address_id):
    address = get_object_or_404(Address, id=address_id, user=request.user)
    form = AddressForm(request.POST, instance=address)

    if form.is_valid():
        with transaction.atomic():
            if form.cleaned_data['is_default']:
                request.user.addresses.exclude(id=address.id).update(is_default=False)
            elif address.is_default and not form.cleaned_data['is_default'] and request.user.addresses.count() > 1:
                first_other_address = request.user.addresses.exclude(id=address.id).first()
                if first_other_address:
                    first_other_address.is_default = True
                    first_other_address.save()

            form.save()
            return JsonResponse({'success': True, 'message': 'Address updated successfully!'})
    else:
        errors = form.errors.as_dict()
        message = 'Please correct the errors.'
        if form.non_field_errors():
            message += ' ' + ' '.join(form.non_field_errors())
        return JsonResponse({'success': False, 'errors': errors, 'message': message}, status=400)


@user_login_required
@require_GET
def get_address_data(request, address_id):
    try:
        address = get_object_or_404(Address, id=address_id, user=request.user)
        data = {
            'id': address.id,
            'full_name': address.full_name,
            'phone': address.phone,
            'house_name': address.house_name,
            'street': address.street,
            'city': address.city,
            'state': address.state,
            'pincode': address.pincode,
            'country': address.country,
            'is_default': address.is_default,
        }
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@user_login_required
@require_POST
def set_default_address(request, address_id):
    try:
        with transaction.atomic():
            request.user.addresses.update(is_default=False)
            address_to_set_default = get_object_or_404(Address, id=address_id, user=request.user)
            address_to_set_default.is_default = True
            address_to_set_default.save()
            return JsonResponse({'success': True, 'message': 'Default address updated.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Error setting default address: {str(e)}'}, status=400)


@user_login_required
@require_POST
def delete_address(request, address_id):
    try:
        address_to_delete = get_object_or_404(Address, id=address_id, user=request.user)

        if request.user.addresses.count() == 1 and address_to_delete.is_default:
            return JsonResponse({'success': False, 'message': 'Cannot delete the only address as it is the default.'}, status=400)

        was_default = address_to_delete.is_default
        address_to_delete.delete()

        if was_default and request.user.addresses.exists():
            new_default = request.user.addresses.order_by('id').first()
            if new_default:
                new_default.is_default = True
                new_default.save()

        return JsonResponse({'success': True, 'message': 'Address deleted successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Error deleting address: {str(e)}'}, status=400)

# --- NEW VIEW FOR NEWSLETTER SUBSCRIPTION ---
@require_POST
def subscribe_newsletter(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        form = NewsletterForm(request.POST) 
        if form.is_valid():
            try:
                form.save() 
                return JsonResponse({'success': True, 'message': 'Thank you for subscribing!'})
            except IntegrityError:
               
                return JsonResponse({'success': False, 'message': 'This email is already subscribed.'}, status=409) # 409 Conflict
            except Exception as e:
                return JsonResponse({'success': False, 'message': 'An unexpected error occurred. Please try again later.'}, status=500)
        else:
            errors = form.errors.as_dict()
            first_error_message = next(iter(errors.values()))[0] if errors else "Invalid email provided."
            return JsonResponse({'success': False, 'message': first_error_message, 'errors': errors}, status=400)
    return JsonResponse({'success': False, 'message': 'Invalid request.'}, status=400)


User = get_user_model() 

def login_view(request):
    if request.user.is_authenticated:
        messages.info(request, "You are already logged in.")
        next_url = request.GET.get('next', 'user_dashboard')
        return redirect(next_url)

    if request.method == 'POST':
        form = UserLoginForm(request=request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user:
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                try:
                    db_cart = Cart.objects.get(user=user)
                    session_cart = request.session.get('cart', {})

                    for item in db_cart.items.select_related('product'):
                        pid = str(item.product.id)
                        if pid in session_cart:
                            session_cart[pid]['quantity'] += item.quantity
                        else:
                            session_cart[pid] = {
                                'name': item.product.name,
                                'price': float(item.product.price),
                                'quantity': item.quantity,
                                'image_url': item.product.images.first().image.url if item.product.images.exists() else '',
                            }

                    request.session['cart'] = session_cart
                    request.session.modified = True
                except Cart.DoesNotExist:
                    pass

                messages.success(request, f"Welcome, {user.username}!")
                next_url = request.GET.get('next', 'user_dashboard') 
                return redirect(next_url)
            else:
                messages.error(request, 'Invalid credentials.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = UserLoginForm()

    return render(request, 'user/auth/login.html', {'form': form})

def register_view(request):
    if request.user.is_authenticated:
        return redirect('user_dashboard')

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            request.session['pending_user_data'] = form.cleaned_data
            request.session['otp_email'] = email

            otp = str(random.randint(100000, 999999))
            EmailOTP.objects.update_or_create(
                email=email,
                defaults={"otp": otp, "purpose": "register", "created_at": timezone.now()}
            )

            send_otp_email(email, otp)
            return redirect('verify_register_otp')

        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.title()}: {error}")
            for error in form.non_field_errors():
                messages.error(request, f"Error: {error}")

    else:
        form = UserRegistrationForm()

    return render(request, 'user/auth/register.html', {'form': form})

@user_login_required
def user_dashboard_view(request):
    return render(request, 'user/main/authenticated_home.html')


def forgot_password_view(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        if User.objects.filter(email=email).exists():
            otp = str(random.randint(100000, 999999))
            EmailOTP.objects.update_or_create(
                email=email,
                defaults={'otp': otp, 'created_at': timezone.now()}
            )
            send_otp_email(email, otp)
            request.session['reset_email'] = email
            return redirect('verify_reset_otp') 
        else:
            messages.error(request, "Email not found.")
    return render(request, 'user/auth/request_reset_otp.html')


def confirm_code_view(request):
    return render(request, 'user/auth/confirm_code.html')


def logout_view(request):
    if '_auth_user_id' in request.session:
        del request.session['_auth_user_id']
    request.session.flush()
    messages.info(request, "You have been logged out.")
    return redirect('home')


# --- Main Pages ---

def home_view(request):
    print("🔍 AUTH USER:", get_user(request))
    print("🔍 IS AUTHENTICATED:", request.user.is_authenticated)
    user = request.user if request.user.is_authenticated else None
    if not user:
        user_id = request.session.get('_auth_user_id')
        if user_id:
            try:
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                request.user = user
            except User.DoesNotExist:
                if '_auth_user_id' in request.session:
                    del request.session['_auth_user_id']
                request.session.flush()
                user = None
                messages.error(request, "Your user session was invalid and has been cleared.")

    new_products = Product.objects.filter(is_sold=False).order_by('-created_at')[:12]
    categories = Category.objects.all()

    wishlisted_product_ids = []
    if user:
        wishlisted_product_ids = Wishlist.objects.filter(user=user).values_list('product_id', flat=True)



    return render(request, 'user/main/home.html', {
        'new_products': new_products,
        'categories': categories,
        'wishlisted_product_ids': list(wishlisted_product_ids),
        
        'request_user': user
    })


@user_login_required
def user_dashboard_view(request):
    return render(request, 'user/main/authenticated_home.html')


def shop_view(request):
    user = request.user if request.user.is_authenticated else None
    if not user:
        user_id = request.session.get('_auth_user_id')
        if user_id:
            try:
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                request.user = user
            except User.DoesNotExist:
                request.session.flush()
                user = None
                messages.error(request, "Your session expired. Please log in again.")

    products_queryset = Product.objects.filter(is_sold=False) 

    categories = Category.objects.all()
    all_sizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL'] 
    selected_categories = request.POST.getlist('category') or request.GET.getlist('category')
    selected_sizes = request.POST.getlist('size[]') or request.GET.getlist('size[]') 
    selected_max_price = request.POST.get('max_price') or request.GET.get('max_price') or '10000' 

    print(f"Request METHOD: {request.method}")
    print(f"Request POST parameters: {request.POST}")
    print(f"Request GET parameters: {request.GET}")
    search_query = request.GET.get('query')
    print(f"Search Query obtained: '{search_query}'")

    q_filters = Q()
    
    if selected_categories:
        q_filters &= Q(category__id__in=selected_categories)
        print(f"Applied category filter: {selected_categories}")

    try:
        if selected_max_price: 
            decimal_max_price = Decimal(selected_max_price)
            q_filters &= Q(price__lte=decimal_max_price)
            print(f"Applied max_price filter: {selected_max_price}")
        else: 
            selected_max_price = '10000' 
    except (TypeError, ValueError):
        print(f"Invalid max_price value provided: '{selected_max_price}'. Ignoring filter.")
        selected_max_price = '10000' 
        pass

    if selected_sizes:
        size_q_objects = Q()
        for s in selected_sizes: 
            size_q_objects |= Q(size__icontains=s)
        q_filters &= size_q_objects
        print(f"Applied size filter for 'size' field (CharField __icontains): {selected_sizes}")

    if search_query:
        print(f"Applying search filter for query: '{search_query}'")
        q_filters &= (Q(name__icontains=search_query) |
                      Q(description__icontains=search_query))
    else:
        print("No search query provided.")

    products = products_queryset.filter(q_filters).order_by('?')

    print(f"Total products after all filters: {products.count()}")

    # Wishlist
    wishlisted_product_ids = []
    if user and user.is_authenticated:
        try:
            wishlisted_product_ids = Wishlist.objects.filter(user=user).values_list('product_id', flat=True)
            print(f"Wishlisted IDs for user {user.username}: {list(wishlisted_product_ids)}")
        except Exception as e:
            print(f"Error fetching wishlist: {e}")
            wishlisted_product_ids = []
    else:
        print("User not authenticated or user object is None, no wishlist fetched.")

    context = {
        'products': products,
        'categories': categories,
        'sizes': all_sizes, 
        'wishlisted_product_ids': list(wishlisted_product_ids),
        'selected_categories': selected_categories,
        'selected_sizes': selected_sizes,
        'selected_max_price': selected_max_price,
        'search_query': search_query,
        'now': timezone.now(),
    }

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'user/main/product_grid_partial.html', context)
    else:
        return render(request, 'user/main/shop.html', context)

def product_detail_view(request, id=None):
    product = get_object_or_404(Product, id=id)

    reservation_expires_in_seconds = 0
    is_reserved_by_current_user = False
    has_subscribed_for_notification = False

    if product.is_currently_reserved:
        if product.reservation_expires_at:
            time_left = product.reservation_expires_at - timezone.now()
            reservation_expires_in_seconds = max(0, int(time_left.total_seconds()))
        if request.user.is_authenticated and product.reserved_by_user == request.user:
            is_reserved_by_current_user = True
        
    if request.user.is_authenticated:
        has_subscribed_for_notification = NotificationSubscription.objects.filter(
            user=request.user,
            product=product,
            event_type='available',
        ).exists()

    # --- Fetch Similar Products ---
    similar_products = Product.objects.filter(
        category=product.category 
    ).exclude(
        id=product.id             
    ).order_by('?')               
    
    similar_products = similar_products[:6]

    context = {
        'product': product,
        'reservation_expires_in_seconds': reservation_expires_in_seconds,
        'is_reserved_by_current_user': is_reserved_by_current_user,
        'has_subscribed_for_notification': has_subscribed_for_notification,
        'similar_products': similar_products, 
    }
    return render(request, 'user/main/product_detail.html', context)


def policy_view(request):
    return render(request, 'user/main/static_pages/policy.html',{
    })


def contact_view(request):
    return render(request, 'user/main/static_pages/contact.html',{
    })


def about_view(request):
    return render(request, 'user/main/static_pages/about.html',{
    })

def cart_page_view(request):
    print("***************** cart_page_view IS BEING CALLED *****************")
    
    cart_items_for_display = [] 
    original_total_price = Decimal('0.00') 
    total_price_after_discounts = Decimal('0.00') 
    applicable_bogo_discount = Decimal('0.00') 
    cart_count = 0 

    products_grouped_by_bogo_offer = {}
    items_for_standard_pricing = []

    try:
        cart = Cart.objects.get(user=request.user)
        
        with transaction.atomic():
            items_to_delete_from_cart = []
            
            cart_queryset = cart.items.select_related('product').prefetch_related('product__offers').all()
            
            for item in list(cart_queryset): 
                product = item.product
                
                is_reserved_by_current_user_flag = False
                is_sold_flag = product.is_sold 
                is_reserved_by_other_flag = False 
                reservation_time_left_seconds = 0
                
                # --- Product Availability Checks ---
                if is_sold_flag: 
                    messages.warning(request, f"'{product.name}' is no longer available as it has been sold and has been removed from your cart.")
                    items_to_delete_from_cart.append(item.id)
                    continue 

                if product.reserved_by_user: 
                    if product.reserved_by_user != request.user:
                        messages.warning(request, f"'{product.name}' is currently reserved by another customer and has been removed from your cart.")
                        items_to_delete_from_cart.append(item.id)
                        is_reserved_by_other_flag = True 
                        continue 
                    elif product.reserved_by_user == request.user:
                        time_left_seconds = product.get_reservation_time_left_seconds() 
                        if time_left_seconds > 0:
                            is_reserved_by_current_user_flag = True
                        else:
                            messages.warning(request, f"Your reservation for '{product.name}' has expired and it has been removed from your cart.")
                            product.release_reservation() 
                            product.save()
                            items_to_delete_from_cart.append(item.id)
                            continue 
                
                original_item_total = product.price * item.quantity
                original_total_price += original_item_total
                cart_count += item.quantity

                common_item_data = {
                    'product': product,
                    'quantity': item.quantity,
                    'original_item_total': original_item_total,
                    'cart_item_id': item.id,
                    'is_reserved_by_current_user': is_reserved_by_current_user_flag,
                    'is_sold': is_sold_flag, 
                    'is_reserved_by_other': is_reserved_by_other_flag, 
                    'reservation_time_left_seconds': reservation_time_left_seconds,
                    'offer_tag': '', 
                    'is_bogo_free': False 
                }

                # --- Offer Application ---
                active_product_offers = [
                    offer for offer in product.offers.all() if offer.is_currently_active
                ]

                bogo_offer_found_for_product = False
                for offer in active_product_offers:
                    if offer.offer_type in ['BOGO1', 'BOGO2']: 
                        offer_key = offer.id
                        if offer_key not in products_grouped_by_bogo_offer:
                            products_grouped_by_bogo_offer[offer_key] = {
                                'offer': offer,
                                'items': [] 
                            }
                        products_grouped_by_bogo_offer[offer_key]['items'].append(common_item_data)
                        bogo_offer_found_for_product = True
                        break 
                
                if not bogo_offer_found_for_product:
                    applied_simple_offer = None
                    for offer in active_product_offers:
                        if offer.offer_type in ['PERCENTAGE', 'AMOUNT']:
                            applied_simple_offer = offer
                            break 
                    
                    common_item_data['applied_offer'] = applied_simple_offer
                    items_for_standard_pricing.append(common_item_data)

            if items_to_delete_from_cart:
                CartItem.objects.filter(id__in=items_to_delete_from_cart).delete()

    except Cart.DoesNotExist:
        pass 

    # --- Calculate discounts from BOGO offers ---
    for offer_key, offer_data in products_grouped_by_bogo_offer.items():
        offer = offer_data['offer']
        items_under_bogo = offer_data['items'] 

        total_bogo_quantity = sum(item['quantity'] for item in items_under_bogo)

        buy_qty = offer.buy_quantity
        get_qty = offer.get_quantity

        if buy_qty is None or get_qty is None or buy_qty <= 0 or get_qty <= 0:
            for item_data in items_under_bogo:
                item_data['offer_tag'] = offer.tag_text 
                cart_items_for_display.append({
                    'product_id': item_data['product'].id,
                    'name': item_data['product'].name,
                    'price': float(item_data['product'].price),
                    'quantity': item_data['quantity'],
                    'total_price': float(item_data['original_item_total']), 
                    'image_url': item_data['product'].images.first().image.url if item_data['product'].images.exists() else '',
                    'is_reserved_by_current_user': item_data['is_reserved_by_current_user'], 
                    'is_sold': item_data['is_sold'], 
                    'is_reserved_by_other': item_data['is_reserved_by_other'], 
                    'reservation_time_left_seconds': item_data['reservation_time_left_seconds'], 
                    'offer_tag': item_data['offer_tag'], 
                })
            continue 

        products_per_cycle = buy_qty + get_qty
        num_cycles = total_bogo_quantity // products_per_cycle
        num_free_products_overall = num_cycles * get_qty

        sorted_bogo_items = sorted(items_under_bogo, key=lambda x: x['product'].price) 

        remaining_free_products_to_distribute = num_free_products_overall

        for item_data in sorted_bogo_items:
            product = item_data['product']
            quantity_in_cart = item_data['quantity']

            if remaining_free_products_to_distribute > 0:
                free_from_this_item = min(quantity_in_cart, remaining_free_products_to_distribute)
                
                item_data['is_bogo_free'] = True 
                item_data['offer_tag'] = offer.tag_text
                
                item_discount_amount = free_from_this_item * product.price
                applicable_bogo_discount += item_discount_amount 

                remaining_free_products_to_distribute -= free_from_this_item
            
            item_display_total = item_data['original_item_total']
            if item_data['is_bogo_free']:
                item_display_total = Decimal('0.00') 

            cart_items_for_display.append({
                'product_id': item_data['product'].id,
                'name': item_data['product'].name,
                'price': float(item_data['product'].price), 
                'quantity': item_data['quantity'],
                'total_price': float(item_display_total), 
                'image_url': item_data['product'].images.first().image.url if item_data['product'].images.exists() else '',
                'is_reserved_by_current_user': item_data['is_reserved_by_current_user'],
                'is_sold': item_data['is_sold'],
                'is_reserved_by_other': item_data['is_reserved_by_other'],
                'reservation_time_left_seconds': item_data['reservation_time_left_seconds'],
                'offer_tag': item_data['offer_tag'], 
            })

    # --- Calculate discounts from Percentage/Amount offers and add regular items ---
    for item_data in items_for_standard_pricing:
        product = item_data['product']
        quantity = item_data['quantity']
        original_item_total = item_data['original_item_total']
        applied_offer = item_data['applied_offer'] 

        item_total_after_offer = original_item_total
        offer_tag = None

        if applied_offer:
            offer_tag = applied_offer.tag_text
            if applied_offer.offer_type == 'PERCENTAGE':
                item_discount = original_item_total * (applied_offer.discount_percentage / Decimal(100))
                item_total_after_offer = original_item_total - item_discount
            elif applied_offer.offer_type == 'AMOUNT':
                item_discount = applied_offer.discount_amount * quantity
                item_total_after_offer = max(Decimal('0.00'), original_item_total - item_discount)
            
        cart_items_for_display.append({
            'product_id': product.id,
            'name': product.name,
            'price': float(product.price), 
            'quantity': quantity,
            'total_price': float(item_total_after_offer), 
            'image_url': product.images.first().image.url if product.images.exists() else '',
            'is_reserved_by_current_user': item_data['is_reserved_by_current_user'],
            'is_sold': item_data['is_sold'],
            'is_reserved_by_other': item_data['is_reserved_by_other'],
            'reservation_time_left_seconds': item_data['reservation_time_left_seconds'],
            'offer_tag': offer_tag, 
        })
    
    # --- Final Calculation of total_price_after_discounts ---
    total_price_after_discounts = sum(Decimal(str(item['total_price'])) for item in cart_items_for_display)

    total_price_after_discounts = max(Decimal('0.00'), total_price_after_discounts)

    cart_items_json_serializable = []
    for item in cart_items_for_display:
        json_item = item.copy() 
        json_item['price'] = float(json_item['price'])
        json_item['total_price'] = float(json_item['total_price'])
        cart_items_json_serializable.append(json_item)

    try:
        cart_items_json = json.dumps(cart_items_json_serializable)
    except Exception as e:
        print(f"ERROR: An error occurred during json.dumps for JS: {e}")
        cart_items_json = "[]" 

    context = {
        'cart_items': cart_items_for_display, 
        'total_price': float(total_price_after_discounts), 
        'cart_count': cart_count,
        'cart_items_json': cart_items_json, 
        'original_subtotal': float(original_total_price), 
        'bogo_discount_amount': float(applicable_bogo_discount), 
    }
    return render(request, 'user/main/cart.html', context)

@require_POST
@user_login_required
@transaction.atomic
def add_to_cart_view(request, product_id):
    try:
        product = Product.objects.select_for_update().get(id=product_id)
    except Product.DoesNotExist:
        message = "Product not found."
        logger.warning(f"Attempt to add non-existent product ID {product_id} to cart by user {request.user.username}")
        messages.error(request, message)
        return JsonResponse({'success': False, 'message': message}, status=404)

    # --- Initial Product State Checks ---
    if product.is_sold:
        message = f"'{product.name}' is already sold."
        logger.info(f"User {request.user.username} tried to add sold product {product.name} (ID: {product.id}) to cart.")
        messages.error(request, message)
        return JsonResponse({'success': False, 'message': message}, status=400)

    user_cart, _ = Cart.objects.get_or_create(user=request.user)

    new_reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)

    cart_item, created_cart_item = CartItem.objects.get_or_create(
        cart=user_cart,
        product=product,
        user=request.user, 
        defaults={
            'quantity': 1,
            'is_reserved': True, 
            'reserved_until': new_reservation_expires_at 
        }
    )

    # --- Reservation Logic ---
    if product.is_currently_reserved:
        if product.reserved_by_user == request.user:
            product.reservation_expires_at = new_reservation_expires_at
            cart_item.reserved_until = new_reservation_expires_at
            cart_item.is_reserved = True

            product.save(update_fields=['reservation_expires_at'])
            cart_item.save(update_fields=['reserved_until', 'is_reserved'])

            message = f"'{product.name}' is already in your cart. Reservation time extended!"
            logger.info(f"User {request.user.username} extended reservation for product {product.name} (ID: {product.id}).")
            messages.info(request, message)
        else:
            message = f"'{product.name}' is currently reserved by another customer. Please try again later."
            logger.warning(f"User {request.user.username} tried to add product {product.name} (ID: {product.id}) reserved by another user ({product.reserved_by_user.username}).")
            messages.warning(request, message)
            return JsonResponse({'success': False, 'message': message}, status=409)
    else:
        product.reserved_by_user = request.user
        product.reservation_expires_at = new_reservation_expires_at
        product.save(update_fields=['reserved_by_user', 'reservation_expires_at'])

        if not created_cart_item: 
            cart_item.is_reserved = True
            cart_item.reserved_until = new_reservation_expires_at
            cart_item.save(update_fields=['is_reserved', 'reserved_until'])

        message = f"'{product.name}' has been reserved for you for {CART_RESERVATION_TIME_MINUTES} minutes and added to your cart."
        logger.info(f"User {request.user.username} reserved product {product.name} (ID: {product.id}).")
        messages.success(request, message)

    updated_cart_count = user_cart.items.aggregate(total_quantity=Sum('quantity'))['total_quantity'] or 0

    cart_total_price = Decimal('0.00')
    current_cart_items_data = [] 

    for item in user_cart.items.select_related('product').all():
        if not item.product.is_sold and (not item.product.is_currently_reserved or item.product.reserved_by_user == request.user):
            cart_total_price += item.product.price * item.quantity

            time_left_seconds = 0
            is_reserved_by_current_user = False
            if item.product.is_currently_reserved and item.product.reserved_by_user == request.user:
                time_left_seconds = item.product.get_reservation_time_left_seconds()
                is_reserved_by_current_user = True

            item_data = {
                'product_id': item.product.id,
                'name': item.product.name,
                'price': float(item.product.price),
                'quantity': item.quantity,
                'image_url': item.product.images.first().image.url if item.product.images.exists() else '',
                'is_reserved_by_current_user': is_reserved_by_current_user,
                'reservation_time_left_seconds': time_left_seconds,
            }
            current_cart_items_data.append(item_data)
        else:
            pass 

    return JsonResponse({
        'success': True,
        'message': message, 
        'cart_count': updated_cart_count,
        'cart_items_data': current_cart_items_data, 
        'cart_total_price': float(cart_total_price),
    })

@require_POST
@user_login_required
def remove_from_cart_view(request, product_id):
    product_id_str = str(product_id) 

    product = get_object_or_404(Product, id=product_id)
    
    if product.reserved_by_user == request.user:
        product.reserved_by_user = None
        product.reservation_expires_at = None
        product.save()

    if request.user.is_authenticated:
        try:
            user_cart = Cart.objects.get(user=request.user)
            deleted_count, _ = CartItem.objects.filter(cart=user_cart, product=product).delete()
            if deleted_count > 0:
                messages.info(request, f"{product.name} removed from your cart.")
            else:
                messages.warning(request, f"{product.name} was not found in your cart.")
        except Cart.DoesNotExist:
            messages.warning(request, "Your cart does not exist.") 
    cart = request.session.get('cart', {})
    if product_id_str in cart:
        product_name = cart[product_id_str]['name']
        del cart[product_id_str]
        request.session['cart'] = cart
        request.session.modified = True
    return redirect('cart_page')

@require_POST
@user_login_required
def buy_now_checkout_view(request, product_id):
    user = request.user # Get the current user
    product = get_object_or_404(Product, id=product_id)
    logger.debug(f"[{user.username}] buy_now_checkout_view called for product {product_id} ({product.name}).")

    # --- Reservation check for Buy Now ---
    if product.is_sold:
        messages.error(request, f"{product.name} is already sold.")
        logger.warning(f"[{user.username}] Product {product.id} already sold during buy-now attempt.")
        return redirect('product_detail', id=product.id)
    
    if product.is_currently_reserved: 
        if product.reserved_by_user == user:
            product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)
            product.save()
            messages.info(request, f"{product.name} is already reserved by you, reservation extended for {CART_RESERVATION_TIME_MINUTES} minutes.")
            logger.debug(f"[{user.username}] Product {product.id} reservation extended for current user.")
        else:
            messages.warning(f"{product.name} is currently reserved by another customer. Please try again later or click 'Notify Me'.")
            logger.warning(f"[{user.username}] Product {product.id} reserved by another user during buy-now attempt.")
            return redirect('product_detail', id=product.id)
    else:
        product.reserved_by_user = user
        product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)
        product.save()
        messages.success(request, f"{product.name} has been reserved for you for {CART_RESERVATION_TIME_MINUTES} minutes.")
        logger.debug(f"[{user.username}] Product {product.id} reserved for current user via buy-now.")


    request.session['buy_now_item'] = {
        'id': product.id,
        'name': product.name,
        'price': float(product.price), 
        'quantity': 1, 
        'image_url': product.images.first().image.url if product.images.exists() else '',
    }
    request.session.modified = True
    
    # --- CRITICAL DEBUG LINE ---
    logger.debug(f"[{user.username}] buy_now_item successfully set in session by buy_now_checkout_view: {request.session.get('buy_now_item')}")

    return redirect('checkout')


@user_login_required 
@require_POST
@transaction.atomic 
def notify_me_view(request, product_id):
    try:
        product = get_object_or_404(Product, id=product_id)
    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Product not found.'}, status=404)

    if product.is_available_for_purchase:
        return JsonResponse({'success': False, 'message': 'This item is currently available for purchase. You can add it to your cart directly.'}, status=400)
    
    if product.reserved_by_user == request.user:
        return JsonResponse({'success': False, 'message': 'You have already reserved this item. No need for notification.'}, status=400)

    try:
        NotificationSubscription.objects.create(
            user=request.user,
            product=product,
            event_type='available' 
        )
        logger.info(f"User {request.user.username} subscribed to notifications for product {product.name} (ID: {product.id}).")
        return JsonResponse({'success': True, 'message': f'You will be notified when {product.name} becomes available.'})
    except IntegrityError:
        logger.info(f"User {request.user.username} already subscribed to notifications for product {product.name} (ID: {product.id}).")
        return JsonResponse({'success': False, 'message': 'You are already subscribed for notifications for this product.'}, status=400)
    except Exception as e:
        logger.error(f"Failed to subscribe user {request.user.username} for product {product.id}: {e}")
        return JsonResponse({'success': False, 'message': f'Failed to subscribe due to an internal error. Please try again later.'}, status=500)



@user_login_required
def wishlist_view(request):
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__category')
    context = {
        'wishlist_items': wishlist_items,
    }
    return render(request, 'user/main/wishlist.html', context)


@user_login_required
@require_POST
def toggle_wishlist(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
        messages.error(request, "Invalid request for wishlist toggle.")
        return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

    try:
        wishlist_item = Wishlist.objects.get(user=request.user, product=product)
        wishlist_item.delete()
        status = 'removed'
        message = 'Product removed from wishlist.'
        messages.info(request, message)
    except Wishlist.DoesNotExist:
        Wishlist.objects.create(user=request.user, product=product)
        status = 'added'
        message = 'Product added to wishlist.'
        messages.success(request, message)
    except Exception as e:
        status = 'error'
        message = f'An error occurred: {str(e)}'
        messages.error(request, message)

    return JsonResponse({'status': status, 'message': message})


@user_login_required
@require_POST
def remove_from_wishlist(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
        messages.error(request, "Invalid request for wishlist removal.")
        return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

    try:
        wishlist_item = Wishlist.objects.get(user=request.user, product=product)
        wishlist_item.delete()
        messages.info(request, f"{product.name} removed from your wishlist.")
        return JsonResponse({'status': 'removed', 'message': 'Product successfully removed from wishlist.'})
    except Wishlist.DoesNotExist:
        messages.warning(request, "Product not found in your wishlist.")
        return JsonResponse({'status': 'error', 'message': 'Product not found in wishlist.'}, status=404)
    except Exception as e:
        messages.error(request, f"An error occurred: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    

@csrf_protect
@require_POST
def send_contact_email(request):
    if not request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest':
        return JsonResponse({'success': False, 'message': 'Invalid request.'}, status=400)

    try:
        data = json.loads(request.body)
        name = data.get('name')
        email = data.get('email')
        message = data.get('message')

        if not all([name, email, message]):
            return JsonResponse({'success': False, 'message': 'All fields are required.'}, status=400)
        if '@' not in email or '.' not in email: 
            return JsonResponse({'success': False, 'message': 'Please enter a valid email address.'}, status=400)

        subject = f"New Contact Form Submission from {name}"
        email_message = f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}"
        from_email = settings.EMAIL_HOST_USER 
        recipient_list = [settings.EMAIL_HOST_USER] 

        send_mail(
            subject,
            email_message,
            from_email,
            recipient_list,
            fail_silently=False, 
        )

        return JsonResponse({'success': True, 'message': 'Your message has been sent successfully!'})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON format.'}, status=400)
    except Exception as e:
        print(f"Error sending contact email: {e}")
        return JsonResponse({'success': False, 'message': 'An unexpected error occurred. Please try again later.'}, status=500)