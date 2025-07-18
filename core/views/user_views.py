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
from core.models import Product, Category, Wishlist, Order, UserProfile, EmailOTP, Address, NewsletterSubscriber, NotificationSubscription, ReturnReason,ReturnItem, ReturnRequest
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

def refund_to_wallet(user, amount):
    with transaction.atomic():
        wallet = user.wallet
        wallet.balance += amount
        wallet.save()

def download_invoice_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    # Construct full absolute URL to the logo
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
    response['Content-Disposition'] = f'attachment; filename="invoice_{order.id}.pdf"'

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

            # Get or create wallet (ensure it exists before crediting)
            wallet, created = Wallet.objects.get_or_create(user=request.user)
            
            # Refund amount to wallet
            wallet.credit(order.total_price)

            # Add wallet transaction record
            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='refund',
                amount=order.total_price,
                reason=f"Refund for cancelled order #{order.custom_order_id or order.id}"
            )

            # --- NEW/UPDATED LOGIC FOR STOCK UPDATION AND IS_SOLD FLAG ---
            for item in order.items.all():
                if item.product:
                    # Increment stock quantity
                    item.product.stock_quantity += item.quantity
                    # Un-tick 'is_sold' checkbox
                    item.product.is_sold = False
                    item.product.save()
                    logger.info(f"Product '{item.product.name}' (ID: {item.product.id}) stock updated to {item.product.stock_quantity}, is_sold set to False due to order cancellation.")
                else:
                    logger.warning(f"Product for OrderItem {item.id} in Order {order.id} not found during cancellation stock update.")
            # --- END NEW/UPDATED LOGIC ---

            logger.info(f"User cancelled order {order.id}. Scheduling cancellation email.")
            # FIX: Call with .delay() and pass order.id
            send_order_cancelled_email.delay(order.id) 

            messages.success(request, f"Order has been cancelled and ₹{order.total_price} has been refunded to your wallet.")
            return redirect('my_profile')

    except Exception as e:
        logger.error(f"Error cancelling order {order_id}: {e}", exc_info=True)
        messages.error(request, 'An unexpected error occurred while cancelling the order.')
        return redirect('my_profile')

# In core/views/admin_views.py (or wherever process_admin_return_request is)
# Ensure this import is correct and points to your tasks file (e.g., core.emails or core.tasks)

@user_login_required
@require_GET
def get_order_items_for_return(request, order_id):
    """
    AJAX endpoint to fetch order items and return reasons for the return modal.
    """
    try:
        order = get_object_or_404(Order, id=order_id, user=request.user)
        
        # Only allow returns for delivered orders that haven't been fully returned/cancelled
        if order.status != 'Delivered':
            return JsonResponse({'status': 'error', 'message': 'Only delivered orders can be returned.'}, status=400)

        # Fetch OrderItems that are not already fully returned
        # An item is eligible if its total requested return quantity across all ReturnRequests
        # associated with it is less than its original quantity.
        eligible_items = []
        for item in order.items.all().select_related('product'):
            # Calculate total quantity already requested for return for this specific OrderItem
            total_requested_qty = ReturnItem.objects.filter(order_item=item, 
                return_request__status__in=['Requested', 'Approved', 'Processing Refund']).aggregate(Sum('quantity'))['quantity__sum'] or 0
            
            # If the item's original quantity is greater than what's already requested
            if item.quantity > total_requested_qty:
                eligible_items.append({
                    'id': item.id,
                    'product_name': item.product.name if item.product else 'Deleted Product',
                    'image_url': item.product.images.first().image.url if item.product and item.product.images.first() else '',
                    'price_at_purchase': str(item.price_at_purchase),
                    'quantity': item.quantity,
                    'max_return_quantity': item.quantity - total_requested_qty, # Quantity still available for return
                })
        
        if not eligible_items:
             return JsonResponse({'status': 'error', 'message': 'No eligible items for return in this order.'}, status=400)

        # Fetch pre-defined return reasons
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
        selected_items_data = data.get('selected_items') # [{'item_id': X, 'quantity': Y}, ...]
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
            # If a custom reason is provided but not required for the chosen reason, ignore it
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
                return_qty = int(item_data.get('quantity', 1)) # Default to 1 if not specified

                order_item = get_object_or_404(OrderItem, id=item_id, order=order)

                # Calculate current returned quantity for this order item across all ReturnRequests
                # that have been 'Requested', 'Approved', or 'Processing Refund'
                current_returned_qty = ReturnItem.objects.filter(
                    order_item=order_item,
                    return_request__status__in=['Requested', 'Approved', 'Processing Refund']
                ).aggregate(Sum('quantity'))['quantity__sum'] or 0

                # Ensure requested return quantity does not exceed available quantity
                if return_qty <= 0 or (current_returned_qty + return_qty) > order_item.quantity:
                    raise ValueError(f"Invalid return quantity {return_qty} for item {order_item.product.name}. Max available for return: {order_item.quantity - current_returned_qty}.")
                
                # Create ReturnItem entry
                ReturnItem.objects.create(
                    return_request=new_return_request,
                    order_item=order_item,
                    quantity=return_qty
                )
            
            # Optional: Update Order status to 'Returned' if all items are covered by return requests.
            # This logic can be more complex, considering partial returns.
            # For simplicity, we'll let admin manage overall order status
            # unless ALL items are requested for return.
            # However, you already had a 'return_status' field on Order, so if you still want
            # to signal something on the Order itself, you could update it here.
            # Since Order doesn't have return_status anymore, we just create the ReturnRequest.
            
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
        valid_from__lte=now, # Corrected field name
        valid_to__gte=now    # Corrected field name
    ).order_by('-created_at')

    coupons_with_applicability = []

    # Get current cart data for applicability checks
    cart = None
    cart_items_queryset = CartItem.objects.none() # Initialize as empty queryset
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
            # Calculate cart_total_price from the filtered queryset
            for item in cart_items_queryset:
                # Assuming CartItem has a 'total_price' attribute or calculate it from product price and quantity
                cart_total_price += item.product.price * item.quantity 
            cart_unique_products_count = cart_items_queryset.values('product').distinct().count()
            cart_total_quantity = cart_items_queryset.aggregate(Sum('quantity'))['quantity__sum'] or 0
        except Cart.DoesNotExist:
            pass # cart_items_queryset remains an empty queryset

    for coupon in all_coupons:
        # Use the coupon's is_valid method to determine applicability and reason
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
        'coupons': coupons_with_applicability, # Pass the list of dicts
    }
    return render(request, 'user/main/coupon_list.html', context)

@user_login_required
def checkout_view(request):
    """
    Handles displaying the checkout page, including order summary,
    address selection, payment methods, and coupon application/validation.
    Prioritizes actual cart items over lingering 'buy_now_item' session data.
    """
    cart_items_for_order = []
    subtotal = Decimal('0.00')
    buy_now_item_data = None # FIX: Initialize buy_now_item_data here

    with transaction.atomic():
        # --- PHASE 1: Always try to process the regular cart items first ---
        try:
            db_cart = Cart.objects.get(user=request.user)
            cart_items_to_delete_ids = []

            for item in db_cart.items.select_for_update().select_related('product'):
                product = item.product

                if product.is_sold:
                    messages.error(request, f"'{product.name}' is already sold and removed from your cart.")
                    cart_items_to_delete_ids.append(item.id) # FIX: Corrected typo here
                    continue

                if product.reserved_by_user:
                    if product.reserved_by_user != request.user:
                        messages.warning(request, f"'{product.name}' is now reserved by another customer and removed from your cart.")
                        cart_items_to_delete_ids.append(item.id)
                        continue
                    elif not product.is_currently_reserved:
                        # FIX: Added request as first argument
                        messages.warning(request, f"Your reservation for '{product.name}' has expired and it was removed from your cart.")
                        product.release_reservation()
                        cart_items_to_delete_ids.append(item.id)
                        continue

                item_total = product.price * item.quantity
                cart_items_for_order.append({
                    'product': product,
                    'quantity': item.quantity,
                    'total_price': item_total
                })
                subtotal += item_total

            if cart_items_to_delete_ids:
                CartItem.objects.filter(id__in=cart_items_to_delete_ids).delete()

            # If we successfully populated cart_items_for_order from db_cart,
            # clear any lingering 'buy_now_item' from session.
            # This ensures the actual cart takes precedence.
            if cart_items_for_order and 'buy_now_item' in request.session:
                del request.session['buy_now_item']
                request.session.modified = True

        except Cart.DoesNotExist:
            pass # No cart, cart_items_for_order remains empty

        # --- PHASE 2: If regular cart is empty, then check for a 'buy_now_item' ---
        # This only runs if PHASE 1 didn't populate cart_items_for_order.
        if not cart_items_for_order:
            buy_now_item_data = request.session.get('buy_now_item') # Now this is a re-assignment, not first assignment
            if buy_now_item_data:
                try:
                    product = Product.objects.select_for_update().get(id=buy_now_item_data['id'])

                    # Re-check product availability for buy-now item
                    if product.is_sold:
                        messages.error(request, f"'{product.name}' from 'Buy Now' is already sold.")
                        del request.session['buy_now_item']
                        request.session.modified = True
                        return redirect('shop') # Or redirect back to product detail

                    if product.reserved_by_user:
                        if product.reserved_by_user != request.user:
                            # FIX: Added request as first argument
                            messages.warning(request, f"'{product.name}' from 'Buy Now' is reserved by another customer.")
                            del request.session['buy_now_item']
                            request.session.modified = True
                            return redirect('product_detail', id=product.id)
                        elif not product.is_currently_reserved:
                            # FIX: Added request as first argument
                            messages.warning(request, f"Your 'Buy Now' reservation for '{product.name}' has expired.")
                            product.release_reservation()
                            del request.session['buy_now_item']
                            request.session.modified = True
                            return redirect('product_detail', id=product.id)
                    else:
                        # If not reserved, reserve it now for buy-now flow
                        product.reserved_by_user = request.user
                        product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)
                        product.save()
                        # FIX: Added request as first argument
                        messages.info(request, f"'{product.name}' reserved for 'Buy Now'.")

                    quantity = buy_now_item_data['quantity']
                    item_total = product.price * quantity
                    cart_items_for_order.append({
                        'product': product,
                        'quantity': quantity,
                        'total_price': item_total
                    })
                    subtotal += item_total

                except Product.DoesNotExist:
                    messages.error(request, "The 'Buy Now' product is no longer available.")
                    del request.session['buy_now_item']
                    request.session.modified = True
                    return redirect('shop')
            # If buy_now_item_data was processed and added to cart_items_for_order, it's already handled.
            # If buy_now_item_data was invalid/non-existent, it's already cleared.
            # So, no further action needed here for buy_now_item.

    if subtotal <= 0 or not cart_items_for_order:
        messages.error(request, "Your cart is empty or contains invalid/expired items.")
        # Ensure buy_now_item is cleared if cart is empty after all checks
        if 'buy_now_item' in request.session:
            del request.session['buy_now_item']
            request.session.modified = True
        return redirect('shop') # Redirect to shop or cart page

    total_price = subtotal
    discount_amount = Decimal('0.00')
    coupon_applied_code = request.session.get('applied_coupon_code')
    applied_coupon_object = None # This will hold the actual Coupon object if valid

    # Get current cart items for coupon validation (as a QuerySet for efficiency)
    user_cart_obj = None
    user_cart_items_queryset = CartItem.objects.none() # Initialize as empty queryset
    try:
        user_cart_obj = Cart.objects.get(user=request.user)
        user_cart_items_queryset = CartItem.objects.filter(
            cart=user_cart_obj,
            product__is_sold=False,
            product__stock_quantity__gt=0
        )
    except Cart.DoesNotExist:
        pass # user_cart_items_queryset remains an empty queryset

    # Handle coupon application initiated from GET request (e.g., from coupon list or direct input)
    if request.method == 'GET' and 'coupon' in request.GET:
        coupon_code_from_url = request.GET.get('coupon', '').strip().upper()
        if coupon_code_from_url:
            try:
                coupon = Coupon.objects.get(code=coupon_code_from_url)

                # Call the coupon's is_valid method with full context
                is_valid, validation_message = coupon.is_valid(
                    subtotal,
                    user=request.user,
                    cart_items=user_cart_items_queryset # Pass QuerySet of cart items
                )

                if is_valid:
                    discount_amount = coupon.discount
                    total_price = coupon.apply_discount(subtotal)
                    request.session['applied_coupon_code'] = coupon.code # Store code in session
                    # FIX: Use Python's f-string formatting for Decimal
                    messages.success(request, f"Coupon '{coupon.code}' applied successfully! You saved ₹{discount_amount:.2f}.")
                    applied_coupon_object = coupon # Store object for template
                else:
                    messages.error(request, f"Coupon '{coupon.code}' not applied: {validation_message}")
                    # If new coupon fails, ensure any old one is removed
                    if 'applied_coupon_code' in request.session:
                        del request.session['applied_coupon_code']
                        coupon_applied_code = None # Clear for template
                        applied_coupon_object = None
            except Coupon.DoesNotExist:
                messages.error(request, "Invalid coupon code.")
                if 'applied_coupon_code' in request.session: # Clear if input was for non-existent coupon
                    del request.session['applied_coupon_code']
                    coupon_applied_code = None
                    applied_coupon_object = None
        else: # 'coupon' parameter was present but empty (user cleared input field)
            if 'applied_coupon_code' in request.session:
                del request.session['applied_coupon_code']
                messages.info(request, "Coupon field cleared.")
            coupon_applied_code = None # Ensure it's clear
            applied_coupon_object = None

    # Re-validate previously applied coupon from session on subsequent loads of checkout page
    # (e.g., after refreshing, or coming from another page)
    elif coupon_applied_code:
        try:
            coupon = Coupon.objects.get(code=coupon_applied_code)
            is_valid, validation_message = coupon.is_valid(
                subtotal,
                user=request.user,
                cart_items=user_cart_items_queryset
            )
            if is_valid:
                discount_amount = coupon.discount
                total_price = coupon.apply_discount(subtotal)
                applied_coupon_object = coupon
            else:
                # If previously applied coupon is no longer valid, remove it
                # FIX: Added request as first argument
                messages.warning(request, f"Applied coupon '{coupon.code}' is no longer valid: {validation_message}. It has been removed.")
                del request.session['applied_coupon_code']
                coupon_applied_code = None
                applied_coupon_object = None
        except Coupon.DoesNotExist:
            # If coupon was deleted from admin, remove from session
            if 'applied_coupon_code' in request.session:
                del request.session['applied_coupon_code']
            # FIX: Added request as first argument
            messages.warning(request, f"The previously applied coupon is invalid or no longer exists. It has been removed.")
            coupon_applied_code = None
            applied_coupon_object = None

    # Wallet Balance
    wallet, created = Wallet.objects.get_or_create(user=request.user)
    wallet_balance = wallet.balance

    # Razorpay details (order_id needs to be generated before payment)
    # This razorpay_order_id is a placeholder for the checkout page.
    # The actual Razorpay order creation happens in your create_order view.
    # Removed the razorpay_client.order.create call from here.
    razorpay_temp_order_id = request.session.get('razorpay_temp_order_id', str(uuid.uuid4()))
    request.session['razorpay_temp_order_id'] = razorpay_temp_order_id

    # Addresses for checkout
    addresses = Address.objects.filter(user=request.user).order_by('-is_default')
    selected_address_id = None
    if addresses.exists():
        default_address = addresses.filter(is_default=True).first()
        if default_address:
            selected_address_id = default_address.id
        else:
            selected_address_id = addresses.first().id


    context = {
        "cart_items": cart_items_for_order,
        "subtotal": subtotal,
        "total_price": total_price,
        "discount": discount_amount,
        "coupon_applied": coupon_applied_code, # String code if any is applied
        "applied_coupon_object": applied_coupon_object, # The actual Coupon object
        "order_id": razorpay_temp_order_id,
        "razorpay_key": settings.RAZORPAY_KEY_ID,
        "addresses": addresses,
        "selected_address_id": selected_address_id,
        "wallet_balance": wallet_balance,
        "buy_now_active": bool(buy_now_item_data)
    }
    return render(request, 'user/main/checkout.html', context)

@user_login_required
@transaction.atomic # Crucial: Ensure all operations are atomic for POST
@csrf_exempt # Keep this if Razorpay is directly POSTing to this URL without CSRF token
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


        # Basic validation for required POST data
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

        # --- Start Processing Items for Order (Buy Now OR Cart) ---
        buy_now_item_session_data = request.session.get('buy_now_item')
        logger.debug(f"[{user.username}] buy_now_item_session_data: {buy_now_item_session_data}")


        if buy_now_item_session_data:
            try:
                # Ensure the product exists and is available
                # Use select_for_update() to lock the product row during transaction
                product = Product.objects.select_for_update().get(id=buy_now_item_session_data['id'])
                quantity = buy_now_item_session_data['quantity']

                logger.debug(f"[{user.username}] Processing buy-now product {product.id} ({product.name}).")

                if product.is_sold:
                    messages.error(request, f"'{product.name}' was sold before your payment could be processed.")
                    logger.warning(f"[{user.username}] Buy-now product {product.id} already sold.")
                    # Do NOT delete from session here; it will be handled after potential order creation.
                    return redirect('shop') # Redirect to shop as item is gone
                
                # Handling existing reservation for the buy-now product
                if product.is_currently_reserved:
                    if product.reserved_by_user != request.user:
                        messages.warning(request, f"'{product.name}' is currently reserved by another customer.")
                        logger.warning(f"[{user.username}] Buy-now product {product.id} reserved by another user.")
                        return redirect('product_detail', id=product.id)
                    # If reserved by current user, but reservation expired (should be handled in checkout, but double-check)
                    elif product.reservation_expires_at and timezone.now() > product.reservation_expires_at:
                        messages.warning(request, f"Your reservation for '{product.name}' has expired. It is now available for others.")
                        product.release_reservation() # Release the expired reservation
                        logger.warning(f"[{user.username}] Buy-now product {product.id} reservation expired.")
                        return redirect('product_detail', id=product.id)
                
                price_at_purchase = product.price # Capture current price
                item_total = price_at_purchase * quantity
                
                cart_items_data.append({
                    'product': product,
                    'quantity': quantity,
                    'price_at_purchase': price_at_purchase,
                    'item_total': item_total
                })
                total_order_price += item_total
                logger.debug(f"[{user.username}] Added buy-now product {product.id} to cart_items_data. Current length: {len(cart_items_data)}. Item total: {item_total}")

                # buy_now_item_session_data will be cleared AFTER successful order creation.

            except Product.DoesNotExist:
                messages.error(request, "The product you tried to buy is no longer available.")
                logger.error(f"[{user.username}] Buy-now product {buy_now_item_session_data['id']} does not exist.")
                # Do NOT delete from session here.
                return redirect('shop')

        else: # Process items from the DB cart
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
                        continue # Skip this item
                    
                    if product.is_currently_reserved:
                        if product.reserved_by_user != user:
                            messages.warning(request, f"'{product.name}' is now reserved by another user and has been removed from your cart.")
                            cart_items_to_delete_ids.append(item.id)
                            logger.warning(f"[{user.username}] Cart product {product.id} reserved by another user.")
                            continue # Skip this item
                        # Corrected: If reserved by current user and reservation expired
                        elif product.reservation_expires_at and timezone.now() > product.reservation_expires_at:
                            messages.warning(request, f"Your reservation for '{product.name}' has expired and it was removed from your cart.")
                            product.release_reservation() # Release the expired reservation
                            cart_items_to_delete_ids.append(item.id)
                            logger.warning(f"[{user.username}] Cart product {product.id} reservation expired.")
                            continue # Skip this item
                    else:
                        # If not reserved, reserve it immediately for this order transaction
                        product.reserved_by_user = user
                        product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES) # Re-reserve for checkout duration
                        product.is_reserved = True
                        product.save()
                        logger.debug(f"[{user.username}] Cart product {product.id} reserved for current user during checkout.")

                    item_total = product.price * item.quantity
                    cart_items_data.append({
                        'product': product,
                        'quantity': item.quantity,
                        'price_at_purchase': product.price, # Capture current price
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
            # If buy-now item was set, it's safer to clear it here if the order truly cannot be placed.
            if buy_now_item_session_data and 'buy_now_item' in request.session:
                del request.session['buy_now_item']
                request.session.modified = True
            return redirect('shop') # Changed redirect to 'shop' as the order cannot proceed.

        # --- Payment Verification / Debit ---
        final_payment_status = 'Pending' # Default payment status for transaction
        initial_order_status = 'Pending' # Default order status

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
                # Verify the payment signature with Razorpay client
                razorpay_client.utility.verify_payment_signature(params_dict)
                final_payment_status = 'Success'
                initial_order_status = 'Processing' # For successful online payments, move to 'Processing'
                logger.info(f"[{user.username}] Razorpay payment {razorpay_payment_id} verified successfully.")
            except Exception as e:
                messages.error(request, "Razorpay payment verification failed. Please try again or contact support.")
                logger.error(f"[{user.username}] Razorpay verification error for RZ_order_id {razorpay_order_id}: {e}", exc_info=True)
                raise e # Re-raise to trigger transaction rollback and then error handling for payment failure

        elif payment_method == 'wallet':
            wallet, created = Wallet.objects.get_or_create(user=user)
            logger.debug(f"[{user.username}] Wallet balance: {wallet.balance}, attempting to debit: {total_order_price}")
            if wallet.balance >= total_order_price:
                if wallet.debit(total_order_price):
                    final_payment_status = 'Success'
                    initial_order_status = 'Processing' # For successful online payments
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
            # Add a check for COD max limit if necessary (already there)
            if total_order_price > 1000: # Example limit for COD
                 messages.error(request, "Cash on Delivery (COD) not available for orders above ₹1000.")
                 logger.warning(f"[{user.username}] COD rejected for order total {total_order_price}.")
                 return redirect('checkout')
            final_payment_status = 'Pending' # COD payment status is always pending initially
            initial_order_status = 'Pending' # Order status for COD is also pending
            logger.info(f"[{user.username}] COD payment method selected. Order will be Pending.")

        else:
            messages.error(request, "Invalid payment method selected.")
            logger.error(f"[{user.username}] Invalid payment method: {payment_method}")
            return redirect('checkout')

        # --- Create the Order and OrderItems ---
        order = None # Initialize order to None
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
                status=initial_order_status # *** NOW USING THE DETERMINED INITIAL STATUS ***
            )
            logger.info(f"[{user.username}] Order {order.id} created successfully with status '{initial_order_status}' and payment status '{final_payment_status}'.")

            for item_data in cart_items_data:
                product = item_data['product']
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=item_data['quantity'],
                    price_at_purchase=item_data['price_at_purchase']
                )
                logger.debug(f"[{user.username}] OrderItem created for Product {product.id} (Qty: {item_data['quantity']}).")
                
                # Update Product Status (mark as sold and clear reservation)
                product.is_sold = True
                product.reserved_by_user = None
                product.reservation_expires_at = None
                product.is_reserved = False # Explicitly set is_reserved to False
                product.save()
                logger.debug(f"[{user.username}] Product {product.id} marked as sold and reservation cleared.")

                # Send product sold email (ensure send_product_sold_email is available)
                # from .tasks import send_product_sold_email
                # send_product_sold_email.delay(product.name, product.id, user.email)
                
            # Clear items from cart/session ONLY after successful order and order items creation
            if not buy_now_item_session_data: # If it was a regular cart purchase
                if 'user_cart' in locals() and user_cart: # Check if user_cart was successfully fetched
                    CartItem.objects.filter(cart=user_cart).delete() # Delete all items from this cart
                    logger.debug(f"[{user.username}] All items cleared from user's cart.")
            else: # If it was a 'buy now' item
                if 'buy_now_item' in request.session:
                    del request.session['buy_now_item']
                    request.session.modified = True
                    logger.debug(f"[{user.username}] 'buy_now_item' cleared from session.")
                
        except IntegrityError as e:
            logger.error(f"[{user.username}] IntegrityError during order creation for user {user.id}: {e}", exc_info=True)
            messages.error(request, "An error occurred while finalizing your order (database integrity). Please try again.")
            raise e # Re-raise to trigger transaction rollback and then error handling
        except Exception as e:
            logger.error(f"[{user.username}] Unhandled exception during order finalization for user {user.id}: {e}", exc_info=True)
            messages.error(request, "An unexpected error occurred during order finalization. Please contact support.")
            raise e # Re-raise to trigger transaction rollback and then error handling


        messages.success(request, f"Order #{order.id} placed successfully!")
        
        # --- NEW: Send order confirmation email (only if order object was successfully created) ---
        if order:
            try:
                send_order_confirmation_email.delay(order.id)
                logger.info(f"[{user.username}] Queued order confirmation email for order {order.id}.")
            except Exception as e:
                logger.error(f"[{user.username}] Failed to queue order confirmation email for order {order.id}: {e}", exc_info=True)
        # --- END NEW ---

        # Store the order ID in the session for the subsequent GET request
        request.session['last_processed_order_id'] = order.id if order else None # Store ID only if order was created
        request.session.modified = True
        logger.debug(f"[{user.username}] Stored order ID {order.id if order else 'None'} in session for redirect.")

        # Redirect to the SAME URL for the GET request
        return redirect('payment_success') # <--- Redirects to the same URL, which will then be a GET request

    elif request.method == 'GET':
        # This block executes when the browser makes the GET request after the redirect
        logger.debug(f"[{user.username}] payment_success_view GET started.")
        order = None
        # Retrieve messages that were set during the POST request
        success_message = None
        # Use list(messages.get_messages(request)) to consume messages once
        current_messages = list(messages.get_messages(request))
        for message in current_messages:
            if message.level == messages.SUCCESS:
                success_message = str(message)
                break 

        last_processed_order_id = request.session.get('last_processed_order_id')
        logger.debug(f"[{user.username}] Retrieved last_processed_order_id from session: {last_processed_order_id}")

        if last_processed_order_id and request.user.is_authenticated:
            try:
                # Fetch the order using the ID stored in the session
                order = Order.objects.get(id=last_processed_order_id, user=request.user)
                logger.info(f"[{user.username}] Successfully fetched order {order.id} for display.")
                # Clear the session data after it's used
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
            return redirect('login') # Or 'shop', based on your preference

        context = {
            'order': order,
            'message': success_message if success_message else "Your payment was successful!",
            'show_invoice_button': True if order else False, # Only show if an order object is available
        }
        # Ensure your template path is correct
        return render(request, 'user/main/payment_success.html', context)

    else:
        # For any other HTTP methods not explicitly handled
        logger.warning(f"[{user.username}] payment_success_view received unsupported method: {request.method}")
        return HttpResponseBadRequest("Invalid request method.")

@csrf_exempt
def payment_failed_view(request):
    return render(request, 'user/main/payment_failed.html')

User = get_user_model()

# ------------------- OTP Registration Views (No change needed) -------------------
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

            # Create user now
            form = UserRegistrationForm(user_data)
            if form.is_valid():
                user = form.save()
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')

                # Clean session
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



# ------------------- OTP Forgot Password Views (No change needed) -------------------
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

    # Ensure Wallet exists for the user
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    wallet_balance = wallet.balance

    # Optional: Show last 10 transactions in wallet tab
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
    # Ensure userprofile exists, create if not (common pattern)
    # The current form `ProfileForm` is based on `User` model, not `UserProfile`.
    # If UserProfile has fields tied to profile form, you'd load it.
    # For now, `instance=request.user` is correct if updating User fields directly.
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

# Your existing add_address view (for AJAX POST submissions)
@user_login_required
@require_POST
def add_address(request):
    # This view will be called via AJAX from add_address_form.html or other places
    if request.user.addresses.count() >= 3:
        return JsonResponse({'success': False, 'message': 'You can add a maximum of 3 addresses.'}, status=400)

    form = AddressForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            address = form.save(commit=False)
            address.user = request.user

            # Logic to set default address
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
    # Check if it's an AJAX request (optional, but good practice if this URL is only for AJAX)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        form = NewsletterForm(request.POST) # Use the form for validation
        if form.is_valid():
            try:
                form.save() # Saves the email to the NewsletterSubscriber model
                return JsonResponse({'success': True, 'message': 'Thank you for subscribing!'})
            except IntegrityError:
                # This catches if the email is already in the database (due to unique=True)
                return JsonResponse({'success': False, 'message': 'This email is already subscribed.'}, status=409) # 409 Conflict
            except Exception as e:
                # Log other unexpected errors
                # print(f"Error subscribing: {e}") # Uncomment for debugging
                return JsonResponse({'success': False, 'message': 'An unexpected error occurred. Please try again later.'}, status=500)
        else:
            # Form is not valid, return validation errors
            errors = form.errors.as_dict()
            # Extract the first error message from the dictionary
            first_error_message = next(iter(errors.values()))[0] if errors else "Invalid email provided."
            return JsonResponse({'success': False, 'message': first_error_message, 'errors': errors}, status=400)
    # If not an AJAX request, return a generic error or redirect
    return JsonResponse({'success': False, 'message': 'Invalid request.'}, status=400)


# @user_login_required
# def my_orders(request):
#     orders = request.user.order_set.all()

#     return render(request, 'user/profile/my_orders.html', {'orders': orders,})


# --- Auth Views (No changes to these sections for address functionality directly) ---
User = get_user_model() # Get the custom user model if you have one

# Your login_view
def login_view(request):
    # Check if the user is ALREADY authenticated using Django's built-in check
    if request.user.is_authenticated:
        messages.info(request, "You are already logged in.")
        # Redirect to the 'next' URL if provided, otherwise to LOGIN_REDIRECT_URL (set in settings)
        next_url = request.GET.get('next', 'user_dashboard')
        return redirect(next_url)

    if request.method == 'POST':
        form = UserLoginForm(request=request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user:
                # *** FIX APPLIED HERE: Specify the backend explicitly ***
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                # No need for manual request.session.save() or response.set_cookie() here

                # Merge DB cart into session cart (this logic is fine, keep it)
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
                next_url = request.GET.get('next', 'user_dashboard') # Get 'next' if present, otherwise default
                return redirect(next_url)
            else:
                messages.error(request, 'Invalid credentials.')
        else:
            # Form errors will be handled by the template if you display them
            messages.error(request, 'Invalid username or password.') # Generic message for invalid form
    else:
        form = UserLoginForm()

    return render(request, 'user/auth/login.html', {'form': form})


# Your register_view
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
            # Assume generate_otp() is defined elsewhere or use random.randint
            otp = str(random.randint(100000, 999999))
            EmailOTP.objects.update_or_create(
                email=email,
                defaults={'otp': otp, 'created_at': timezone.now()}
            )
            send_otp_email(email, otp)
            request.session['reset_email'] = email
            return redirect('verify_reset_otp') # Assuming verify_reset_otp exists and is correctly named.
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


# --- Main Pages (No changes needed, but ensure cart_count is consistent) ---

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

    products_queryset = Product.objects.filter(is_sold=False) # Start with only non-sold products

    categories = Category.objects.all()
    all_sizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL'] # Define your available sizes

    # Filters - Ensure to get both POST (AJAX) and GET (initial load/direct link)
    selected_categories = request.POST.getlist('category') or request.GET.getlist('category')
    selected_sizes = request.POST.getlist('size[]') or request.GET.getlist('size[]') # Correct name for size checkboxes
    selected_max_price = request.POST.get('max_price') or request.GET.get('max_price') or '10000' # Consistent variable name

    # --- DEBUGGING STEP: Print the raw GET/POST parameters and the search_query ---
    print(f"Request METHOD: {request.method}")
    print(f"Request POST parameters: {request.POST}")
    print(f"Request GET parameters: {request.GET}")
    search_query = request.GET.get('query') # Search query always comes via GET if it's in the URL
    print(f"Search Query obtained: '{search_query}'")
    # --- END DEBUGGING STEP ---

    # Initialize an empty Q object for combined filters
    q_filters = Q()
    
    if selected_categories:
        q_filters &= Q(category__id__in=selected_categories)
        print(f"Applied category filter: {selected_categories}")

    try:
        if selected_max_price: # Check if it's not empty
            decimal_max_price = Decimal(selected_max_price)
            q_filters &= Q(price__lte=decimal_max_price)
            print(f"Applied max_price filter: {selected_max_price}")
        else: # If max_price is empty, revert to default or handle as no max limit
            selected_max_price = '10000' # Ensure the value for context is correct
    except (TypeError, ValueError):
        print(f"Invalid max_price value provided: '{selected_max_price}'. Ignoring filter.")
        selected_max_price = '10000' # Default if conversion fails
        pass

    # --- CORRECTED SIZE FILTERING LOGIC for size (CharField) ---
    if selected_sizes:
        size_q_objects = Q()
        for s in selected_sizes: # 's' for single selected size
            # Use __icontains to check if the 'size' CharField contains the selected size.
            # This is robust for single sizes ('S') or comma-separated ('S,M,L').
            # It will match if product.size is 'S' and 'S' is selected.
            # It will also match if product.size is 'S,M' and 'S' is selected.
            size_q_objects |= Q(size__icontains=s)
        q_filters &= size_q_objects
        print(f"Applied size filter for 'size' field (CharField __icontains): {selected_sizes}")
    # --- END CORRECTED SIZE FILTERING LOGIC ---

    # Apply Search Query Filter
    if search_query:
        print(f"Applying search filter for query: '{search_query}'")
        q_filters &= (Q(name__icontains=search_query) |
                      Q(description__icontains=search_query))
    else:
        print("No search query provided.")

    # Apply all accumulated filters to the initial queryset, then randomize
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
        'sizes': all_sizes, # Use 'all_sizes' here for the filter UI
        'wishlisted_product_ids': list(wishlisted_product_ids),
        'selected_categories': selected_categories,
        'selected_sizes': selected_sizes,
        'selected_max_price': selected_max_price,
        'search_query': search_query,
    }

    # Ensure your partial template filename matches this.
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'user/main/product_grid_partial.html', context)
    else:
        return render(request, 'user/main/shop.html', context)

def product_detail_view(request, id=None):
    product = get_object_or_404(Product, id=id)

    reservation_expires_in_seconds = 0
    is_reserved_by_current_user = False
    has_subscribed_for_notification = False

    # Check if the product has an active reservation by *anyone*
    if product.is_currently_reserved:
        # If it's reserved, calculate time left regardless of who reserved it,
        # but only pass to template if current user.
        if product.reservation_expires_at:
            time_left = product.reservation_expires_at - timezone.now()
            # Ensure seconds are not negative
            reservation_expires_in_seconds = max(0, int(time_left.total_seconds()))

        # Determine if the active reservation is by the current logged-in user
        if request.user.is_authenticated and product.reserved_by_user == request.user:
            is_reserved_by_current_user = True
        
    # If the user is authenticated, check if they have an active notification subscription
    if request.user.is_authenticated:
        has_subscribed_for_notification = NotificationSubscription.objects.filter(
            user=request.user,
            product=product,
            event_type='available',
            # You might want to filter out subscriptions that have already been notified
            # e.g., notified_at__isnull=True if you track notification delivery
        ).exists()

    # --- NEW: Fetch Similar Products ---
    similar_products = Product.objects.filter(
        category=product.category # Same category
    ).exclude(
        id=product.id             # Exclude the current product
    ).order_by('?')               # Optional: order randomly
    
    # Limit the number of similar products to display (e.g., 6)
    similar_products = similar_products[:6]
    # --- END NEW ---

    context = {
        'product': product,
        'reservation_expires_in_seconds': reservation_expires_in_seconds,
        'is_reserved_by_current_user': is_reserved_by_current_user,
        'has_subscribed_for_notification': has_subscribed_for_notification,
        'similar_products': similar_products, # Add similar products to context
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

# core/views/user_views.py (or wherever cart_page_view is defined)

def cart_page_view(request):
    print("***************** cart_page_view IS BEING CALLED *****************")
    cart_items_for_template = []
    total_price = Decimal('0.00')
    cart_count = 0
    
    # Initialize a list to hold data specifically for JavaScript
    cart_items_data_for_js = [] 

    try:
        cart = Cart.objects.get(user=request.user)
        
        with transaction.atomic():
            items_to_delete_from_cart = []
            
            # Use list() to iterate over a copy, allowing modification of the original queryset implicitly through deletion
            for item in list(cart.items.select_related('product')): 
                product = item.product
                
                # IMPORTANT: Consolidate reservation status checks here for clarity
                is_reserved_by_current_user_flag = False
                time_left_seconds = 0
                
                # Check if product is sold
                if product.is_sold:
                    messages.warning(request, f"'{product.name}' is no longer available as it has been sold and has been removed from your cart.")
                    items_to_delete_from_cart.append(item.id)
                    continue # Skip to next item

                # Check if product is reserved by someone else (or if current user's reservation expired)
                if product.is_currently_reserved:
                    if product.reserved_by_user != request.user:
                        # Product is reserved by someone else
                        messages.warning(request, f"'{product.name}' is currently reserved by another customer and has been removed from your cart.")
                        items_to_delete_from_cart.append(item.id)
                        continue # Skip to next item
                    elif product.reserved_by_user == request.user:
                        # Product is reserved by the current user
                        time_left_seconds = product.get_reservation_time_left_seconds()
                        if time_left_seconds > 0:
                            is_reserved_by_current_user_flag = True
                        else:
                            # Current user's reservation has expired (frontend check)
                            messages.warning(request, f"Your reservation for '{product.name}' has expired and it has been removed from your cart.")
                            items_to_delete_from_cart.append(item.id)
                            # The Celery task will handle clearing product.reserved_by_user etc.
                            continue # Skip to next item
                
                # If we reach here, the item is valid for display and processing
                item_total = product.price * item.quantity
                total_price += item_total
                cart_count += item.quantity

                # Populate the list for the Django template (might include CartItem objects directly)
                cart_items_for_template.append({
                    'product_id': product.id,
                    'name': product.name,
                    'price': float(product.price),
                    'quantity': item.quantity,
                    'total': float(item_total),
                    'image_url': product.images.first().image.url if product.images.exists() else '',
                    'is_reserved_by_current_user': is_reserved_by_current_user_flag,
                    'is_sold': product.is_sold,
                    # This flag should truly reflect if it's reserved by someone ELSE
                    'is_reserved_by_other': (product.is_currently_reserved and not is_reserved_by_current_user_flag), 
                    'reservation_time_left_seconds': time_left_seconds,
                    # You might also want to include the item.id here for frontend "Remove" button form action
                    # 'cart_item_id': item.id, 
                })

            # Delete invalid items from the database cart
            if items_to_delete_from_cart:
                CartItem.objects.filter(id__in=items_to_delete_from_cart).delete()
                # The message will already be shown in the loop, maybe refine this message.
                # messages.info(request, "Some items were removed from your cart due to availability changes.")

    except Cart.DoesNotExist:
        pass # Cart doesn't exist for the user, so it's empty

    # DEBUGGING PRINTS
    print(f"DEBUG: Length of cart_items_for_template list: {len(cart_items_for_template)}")
    if cart_items_for_template:
        print("DEBUG: Content of the FIRST item in cart_items_for_template:")
        print(cart_items_for_template[0])
        try:
            # THIS IS THE CRITICAL CHANGE: json.dumps for JavaScript
            cart_items_json = json.dumps(cart_items_for_template)
            print("DEBUG: json.dumps executed successfully.")
            # Print the JSON string itself for verification
            # print("DEBUG: JSON for JS: ", cart_items_json) 
        except Exception as e:
            print(f"ERROR: An error occurred during json.dumps for JS: {e}")
            cart_items_json = "[]" # Fallback to empty array
    else:
        print("DEBUG: cart_items_for_template list is empty. No valid items were found for the cart.")
        cart_items_json = "[]" # Always provide an empty array if no items

    context = {
        'cart_items': cart_items_for_template, # This is for your Django template loop
        'total_price': float(total_price),
        'cart_count': cart_count,
        'cart_items_json': cart_items_json, # <--- NEW CONTEXT VARIABLE FOR JAVASCRIPT
    }
    return render(request, 'user/main/cart.html', context)


@require_POST
@user_login_required
@transaction.atomic
def add_to_cart_view(request, product_id):
    try:
        # Use select_for_update to lock the product row during the transaction
        # This prevents race conditions where multiple users try to reserve the same unique product.
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

    # Get or create the user's cart
    user_cart, _ = Cart.objects.get_or_create(user=request.user)

    # Define the reservation expiry time
    new_reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)

    # Get or create the CartItem for this product in the user's cart.
    # For a single-item reservation, quantity will typically be 1.
    # Set default reservation status on creation.
    cart_item, created_cart_item = CartItem.objects.get_or_create(
        cart=user_cart,
        product=product,
        user=request.user,  # <--- THIS IS THE **ESSENTIAL LINE TO ADD/CHANGE**
        defaults={
            'quantity': 1,
            'is_reserved': True, # Mark as reserved by default when adding
            'reserved_until': new_reservation_expires_at # Set initial expiry
        }
    )

    # --- Reservation Logic ---
    if product.is_currently_reserved:
        if product.reserved_by_user == request.user:
            # Case 1: Product is already reserved by the current user.
            # Extend the reservation time for both Product and CartItem.
            product.reservation_expires_at = new_reservation_expires_at
            cart_item.reserved_until = new_reservation_expires_at
            # Ensure is_reserved is True on cart_item if it somehow became False
            cart_item.is_reserved = True

            product.save(update_fields=['reservation_expires_at'])
            cart_item.save(update_fields=['reserved_until', 'is_reserved'])

            message = f"'{product.name}' is already in your cart. Reservation time extended!"
            logger.info(f"User {request.user.username} extended reservation for product {product.name} (ID: {product.id}).")
            messages.info(request, message)
        else:
            # Case 2: Product is reserved by another user.
            message = f"'{product.name}' is currently reserved by another customer. Please try again later."
            logger.warning(f"User {request.user.username} tried to add product {product.name} (ID: {product.id}) reserved by another user ({product.reserved_by_user.username}).")
            messages.warning(request, message)
            return JsonResponse({'success': False, 'message': message}, status=409)
    else:
        # Case 3: Product is NOT sold and NOT actively reserved by anyone.
        # Reserve it for the current user.
        product.reserved_by_user = request.user
        product.reservation_expires_at = new_reservation_expires_at
        product.save(update_fields=['reserved_by_user', 'reservation_expires_at'])

        # If cart_item was just created, its defaults already set is_reserved and reserved_until.
        # If it already existed but was not reserved (e.g., old expired item), update it here.
        if not created_cart_item: # If item already existed
            cart_item.is_reserved = True
            cart_item.reserved_until = new_reservation_expires_at
            cart_item.save(update_fields=['is_reserved', 'reserved_until'])

        message = f"'{product.name}' has been reserved for you for {CART_RESERVATION_TIME_MINUTES} minutes and added to your cart."
        logger.info(f"User {request.user.username} reserved product {product.name} (ID: {product.id}).")
        messages.success(request, message)

    # --- Prepare data for JSON response (Mini-Cart Update) ---
    # Recalculate total quantity of items in the cart (including non-reserved items)
    updated_cart_count = user_cart.items.aggregate(total_quantity=Sum('quantity'))['total_quantity'] or 0

    # Recalculate total price of the entire cart and get data for all items
    cart_total_price = Decimal('0.00')
    current_cart_items_data = [] # This will hold data for all items currently in the user's cart

    # Iterate through all items in the user's cart to prepare data for the mini-cart
    # Use select_related('product', 'product__images') if images are directly related
    # or ensure Product.images.exists() is performant.
    for item in user_cart.items.select_related('product').all():
        # Only include items that are not sold and, if reserved, are reserved by the current user.
        # Items reserved by others or expired should not be shown in the current user's active cart.
        if not item.product.is_sold and (not item.product.is_currently_reserved or item.product.reserved_by_user == request.user):
            cart_total_price += item.product.price * item.quantity

            # Determine reservation time left for *this specific item* in the mini-cart
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
            # OPTIONAL: If an item in the cart is now sold or reserved by others, you might want
            # to automatically remove it from the cart here for a very clean UX.
            # E.g., item.delete()
            pass # Or handle expired/sold items in the cart if your system allows

    return JsonResponse({
        'success': True,
        'message': message, # Message to show the user (e.g., "Added to cart!")
        'cart_count': updated_cart_count,
        'cart_items_data': current_cart_items_data, # Data for all valid items in cart to populate mini-cart
        'cart_total_price': float(cart_total_price),
    })

@require_POST
@user_login_required
def remove_from_cart_view(request, product_id):
    product_id_str = str(product_id) # Convert to string for session cart lookup

    product = get_object_or_404(Product, id=product_id)
    
    # Check if the product is reserved by the current user
    if product.reserved_by_user == request.user:
        # Release the reservation
        product.reserved_by_user = None
        product.reservation_expires_at = None
        # product.is_reserved = False # This flag might be managed by Product properties
                                    # If you have an @property for is_reserved, no need to set here.
                                    # If not, ensure it's updated.
        product.save()

        # IMPORTANT: Remove or comment out the problematic line:
        # send_product_removed_from_cart_email.delay(product.id) # Async task
        # Notification to other users about availability is handled by Celery's
        # `release_expired_reservations_task` which sends `send_product_available_email`.

    # Remove from DB cart for authenticated users
    # This block executes even if the reservation wasn't released (e.g., product wasn't reserved by current user)
    if request.user.is_authenticated:
        try:
            user_cart = Cart.objects.get(user=request.user)
            # Find the specific CartItem for this product and delete it
            # Using filter().delete() is more robust than .get().delete() if item might not exist.
            deleted_count, _ = CartItem.objects.filter(cart=user_cart, product=product).delete()
            if deleted_count > 0:
                messages.info(request, f"{product.name} removed from your cart.")
            else:
                messages.warning(request, f"{product.name} was not found in your cart.")
        except Cart.DoesNotExist:
            messages.warning(request, "Your cart does not exist.") # Should ideally not happen if user has items
    
    # --- Session Cart removal (Keep this if you still use session cart for display before login) ---
    # This part should ideally be phased out if you primarily rely on DB cart for authenticated users.
    # It's primarily for anonymous users or initial state.
    cart = request.session.get('cart', {})
    if product_id_str in cart:
        product_name = cart[product_id_str]['name']
        del cart[product_id_str]
        request.session['cart'] = cart
        request.session.modified = True
        # messages.info(request, f"{product_name} removed from your session cart.") # Message might be redundant with DB cart message
    # ---------------------------------------------------------------------------------------------------

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
    
    # Check if the product is reserved by anyone, and if so, by whom
    if product.is_currently_reserved: # Use the property here
        if product.reserved_by_user == user:
            # User already reserved it via buy now or cart, extend reservation
            product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)
            product.save()
            messages.info(request, f"{product.name} is already reserved by you, reservation extended for {CART_RESERVATION_TIME_MINUTES} minutes.")
            logger.debug(f"[{user.username}] Product {product.id} reservation extended for current user.")
        else:
            messages.warning(f"{product.name} is currently reserved by another customer. Please try again later or click 'Notify Me'.")
            logger.warning(f"[{user.username}] Product {product.id} reserved by another user during buy-now attempt.")
            return redirect('product_detail', id=product.id)
    else:
        # If not reserved, reserve the product immediately for "Buy Now"
        product.reserved_by_user = user
        product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)
        # product.is_reserved = True # This field does not exist on Product model
        product.save()
        messages.success(request, f"{product.name} has been reserved for you for {CART_RESERVATION_TIME_MINUTES} minutes.")
        logger.debug(f"[{user.username}] Product {product.id} reserved for current user via buy-now.")


    # Save buy now item as a separate session entry
    request.session['buy_now_item'] = {
        'id': product.id,
        'name': product.name,
        'price': float(product.price), # Ensure price is convertible and can be stored
        'quantity': 1, # Always 1 for single product
        'image_url': product.images.first().image.url if product.images.exists() else '',
    }
    request.session.modified = True
    
    # --- CRITICAL DEBUG LINE ---
    logger.debug(f"[{user.username}] buy_now_item successfully set in session by buy_now_checkout_view: {request.session.get('buy_now_item')}")

    return redirect('checkout')


@user_login_required # This decorator already ensures the user is authenticated
@require_POST
@transaction.atomic # Ensure atomic operation for subscription
def notify_me_view(request, product_id):
    try:
        product = get_object_or_404(Product, id=product_id)
    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Product not found.'}, status=404)

    # Use the product's properties for clearer logic
    # User can only subscribe if the product is NOT available for purchase by anyone
    # (i.e., it's sold OR actively reserved by another user).
    if product.is_available_for_purchase:
        # If it's available, no need to notify.
        return JsonResponse({'success': False, 'message': 'This item is currently available for purchase. You can add it to your cart directly.'}, status=400)
    
    # Check if the product is reserved by the current user
    if product.reserved_by_user == request.user:
        return JsonResponse({'success': False, 'message': 'You have already reserved this item. No need for notification.'}, status=400)

    try:
        # Try to create the subscription. unique_together will prevent duplicates.
        NotificationSubscription.objects.create(
            user=request.user,
            product=product,
            event_type='available' # Assuming 'available' is the primary notification type
        )
        logger.info(f"User {request.user.username} subscribed to notifications for product {product.name} (ID: {product.id}).")
        return JsonResponse({'success': True, 'message': f'You will be notified when {product.name} becomes available.'})
    except IntegrityError:
        # This specific exception is caught if unique_together constraint is violated
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

        # Basic server-side validation
        if not all([name, email, message]):
            return JsonResponse({'success': False, 'message': 'All fields are required.'}, status=400)
        if '@' not in email or '.' not in email: # More robust validation can be added
            return JsonResponse({'success': False, 'message': 'Please enter a valid email address.'}, status=400)

        # Compose the email
        subject = f"New Contact Form Submission from {name}"
        email_message = f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}"
        from_email = settings.EMAIL_HOST_USER # Sender email defined in settings.py
        recipient_list = [settings.EMAIL_HOST_USER] # Send to your own email address

        # Send the email
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