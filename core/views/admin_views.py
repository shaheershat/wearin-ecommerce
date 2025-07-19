from django.contrib.auth import login  
from django.db.models import Sum
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
from collections import defaultdict
import json 
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from datetime import timedelta
from django.views.decorators.clickjacking import xframe_options_exempt
from django.contrib import messages
from core.tasks import send_return_processed_email
from django.views.decorators.http import require_GET
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from core.decorators import admin_login_required
from core.models import Order, OrderItem, Product, Address, Category, ProductImage, Coupon,Offer
from core.forms import ProductForm, CouponForm
from django.db.models import Count
from django.utils.timezone import now
from django.db.models.functions import TruncDate
from django.template.loader import render_to_string
from datetime import datetime
import pandas as pd
from io import BytesIO
from django.http import HttpResponse
from django.db.models.functions import TruncDay, TruncMonth
from reportlab.pdfgen import canvas
from core.models import Order, WalletTransaction, Wallet
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.template import Template, Context 
from django.db.models import Q 
from core.models import EmailTemplate, NewsletterCampaign, NewsletterSubscriber ,ReturnRequest, ReturnReason, ReturnItem, OfferBanner, Offer
from core.forms import EmailTemplateForm, NewsletterCampaignForm , OfferBannerForm, OfferForm, ProductFilterForm
from core.tasks import send_newsletter_task 
from django.utils.decorators import method_decorator 
import logging
from django.db import transaction

logger = logging.getLogger(__name__)

@admin_login_required
def offer_management_view(request):
    """
    Custom admin page for managing offer banners and product offers.
    Allows creating/editing banner, applying/removing offers from products,
    and viewing active offers.
    """
    # --- Offer Banner Management ---
    offer_banner_instance, created = OfferBanner.objects.get_or_create(pk=1, defaults={'text_content': 'Limited Time Offer!', 'is_active': False})
    
    if request.method == 'POST' and 'banner_submit' in request.POST:
        banner_form = OfferBannerForm(request.POST, instance=offer_banner_instance)
        if banner_form.is_valid():
            banner_form.save()
            messages.success(request, "Offer banner updated successfully!")
            return redirect('admin_offer_management')
        else:
            messages.error(request, "Error updating offer banner. Please check the form.")
    else:
        banner_form = OfferBannerForm(instance=offer_banner_instance)

    active_live_banner = OfferBanner.objects.filter(is_active=True).first()


    # --- Product Offer Management ---
    offer_form = OfferForm() # Form for creating new offers in the modal
    product_filter_form = ProductFilterForm(request.GET)

    products_queryset = Product.objects.all().order_by('-created_at')

    # Apply filters
    if product_filter_form.is_valid():
        query = product_filter_form.cleaned_data.get('query')
        category = product_filter_form.cleaned_data.get('category')
        sort_by = product_filter_form.cleaned_data.get('sort_by')
        in_offer = product_filter_form.cleaned_data.get('in_offer')

        if query:
            products_queryset = products_queryset.filter(name__icontains=query)
        if category:
            products_queryset = products_queryset.filter(category=category)
        
        if in_offer == 'yes':
            products_queryset = products_queryset.filter(offers__isnull=False).distinct()
        elif in_offer == 'no':
            products_queryset = products_queryset.filter(offers__isnull=True)

        if sort_by:
            products_queryset = products_queryset.order_by(sort_by)

    # Handle product offer actions (Apply Existing Offer, Remove Offer, Create New Offer via AJAX)
    if request.method == 'POST':
        # --- Handle Create New Offer Modal Submission (AJAX) ---
        if 'create_new_offer_modal' in request.POST:
            offer_form = OfferForm(request.POST)
            if offer_form.is_valid():
                offer = offer_form.save()
                return JsonResponse({
                    'status': 'success',
                    'message': f"Offer '{offer.name}' created successfully!",
                    'offer': {
                        'id': offer.id,
                        'name': offer.name, # Use 'name' for display in dropdown
                    }
                })
            else:
                errors = offer_form.errors.as_json()
                return JsonResponse({
                    'status': 'error',
                    'message': 'Validation failed.',
                    'errors': errors
                }, status=400)

        # For the following actions, products must be selected
        selected_product_ids = request.POST.getlist('selected_products')
        if not selected_product_ids:
            messages.warning(request, "Please select at least one product.")
            return redirect('admin_offer_management')

        try:
            with transaction.atomic():
                if 'apply_existing_offer' in request.POST:
                    offer_id = request.POST.get('existing_offer_id')
                    if offer_id:
                        existing_offer = get_object_or_404(Offer, id=offer_id)
                        products_to_update = Product.objects.filter(id__in=selected_product_ids)
                        existing_offer.products.add(*products_to_update)
                        messages.success(request, f"Existing offer '{existing_offer.name}' applied to {len(selected_product_ids)} products.")
                        return redirect('admin_offer_management')
                    else:
                        messages.warning(request, "Please select an existing offer.")

                elif 'remove_offer' in request.POST:
                    offer_id_to_remove = request.POST.get('offer_to_remove_id')
                    
                    products_to_update = Product.objects.filter(id__in=selected_product_ids)

                    if offer_id_to_remove == 'all':
                        for product in products_to_update:
                            product.offers.clear()
                        messages.success(request, f"All offers removed from {len(selected_product_ids)} selected products.")
                    elif offer_id_to_remove:
                        specific_offer = get_object_or_404(Offer, id=offer_id_to_remove)
                        for product in products_to_update:
                            if specific_offer in product.offers.all():
                                product.offers.remove(specific_offer)
                        messages.success(request, f"Offer '{specific_offer.name}' removed from {len(selected_product_ids)} selected products.")
                    else:
                        messages.warning(request, "Please select an offer to remove or choose 'All Offers'.")
                    return redirect('admin_offer_management')

        except Exception as e:
            logger.error(f"Error processing offer action: {e}", exc_info=True)
            messages.error(request, f"An unexpected error occurred: {e}")
            return redirect('admin_offer_management')


    # --- Active Offers List ---
    active_offers = Offer.objects.filter(is_active=True).prefetch_related('products').order_by('-start_date')

    context = {
        'banner_form': banner_form,
        'offer_form': offer_form,
        'product_filter_form': product_filter_form,
        'products': products_queryset.distinct(),
        'active_offers': active_offers,
        'all_offers': Offer.objects.all().order_by('name'),
        'active_live_banner': active_live_banner,
    }
    return render(request, 'admin_panel/offer_management.html', context)


@admin_login_required
def edit_offer_view(request, offer_id):
    """
    Handles editing an existing offer.
    GET: Renders the edit form populated with existing offer data.
    POST: Processes the form submission to update the offer.
    """
    try:
        offer = get_object_or_404(Offer, id=offer_id)
        if request.method == 'POST':
            form = OfferForm(request.POST, instance=offer)
            if form.is_valid():
                form.save()
                messages.success(request, f"Offer '{offer.name}' updated successfully!")
                return redirect('admin_offer_management')
            else:
                messages.error(request, 'Please correct the errors below.')
        else:
            form = OfferForm(instance=offer)
        return render(request, 'admin_panel/offer_edit.html', {'form': form, 'offer': offer})
    except Exception as e:
        logger.error(f"Error in edit_offer_view for offer {offer_id}: {e}", exc_info=True)
        messages.error(request, f"An unexpected error occurred: {e}")
        return render(request, 'admin_panel/offer_edit.html', {'form': form, 'offer': offer})
    
@admin_login_required    
def delete_offer_view(request, offer_id):
    """
    Handles deleting an offer.
    GET: Renders the delete confirmation page.
    POST: Deletes the offer and redirects to the offer management page.
    """
    try:
        offer = get_object_or_404(Offer, id=offer_id)
        if request.method == 'POST':
            offer_name = offer.name
            offer.delete()
            messages.success(request, f"Offer '{offer_name}' deleted successfully!")
            return redirect('admin_offer_management')
        return render(request, 'admin_panel/offer_delete.html', {'offer': offer})
    except Exception as e:
        logger.error(f"Error deleting offer {offer_id}: {e}", exc_info=True)
        messages.error(request, f"Failed to delete offer due to server error: {e}")
        return render(request, 'admin_panel/offer_delete.html', {'offer': offer})

@admin_login_required
@require_GET
def get_return_request_for_admin_modal(request, request_id):
    """
    Renders the admin_panel/order_return_modal.html directly
    with the return request details, instead of returning JSON.
    """
    try:
        return_request = get_object_or_404(
            ReturnRequest.objects.select_related('user', 'order', 'reason'),
            id=request_id
        )

        total_refund_calculated = Decimal('0.00')
        for ri in return_request.requested_items.all().select_related('order_item__product'):
            if ri.order_item and ri.order_item.product:
                total_refund_calculated += ri.quantity * ri.order_item.price_at_purchase

        context = {
            'return_request': return_request,
            'total_refund_calculated': total_refund_calculated,
        }
        
        return render(request, 'admin_panel/order_return_modal.html', context)

    except ReturnRequest.DoesNotExist:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return HttpResponse("Return request not found.", status=404)
        messages.error(request, "Return request not found.")
        return redirect('admin_order_list')
    except Exception as e:
        logger.error(f"Error fetching return request details for admin: {e}", exc_info=True)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return HttpResponse("An unexpected error occurred.", status=500)
        messages.error(request, "An unexpected error occurred while loading return details.")
        return redirect('admin_order_list')


@admin_login_required
@require_POST
def process_admin_return_request(request, request_id):
    """
    AJAX endpoint for admin to approve or reject a return request.
    """
    try:
        data = json.loads(request.body)
        action = data.get('action') # 'approve' or 'reject'
        admin_notes = data.get('admin_notes', '').strip()

        if action not in ['approve', 'reject']:
            return JsonResponse({'status': 'error', 'message': 'Invalid action.'}, status=400)

        return_request = get_object_or_404(ReturnRequest, id=request_id)
        order = return_request.order # Get the associated Order

        if return_request.status not in ['Requested', 'Processing Refund']:
            return JsonResponse({'status': 'error', 'message': f'Return request is already {return_request.status}.'}, status=400)

        with transaction.atomic():
            return_request.reviewed_at = timezone.now()
            return_request.admin_notes = admin_notes

            if action == 'approve':
                total_refund_amount = Decimal('0.00')

                for ri in return_request.requested_items.all().select_related('order_item__product'):
                    order_item = ri.order_item
                    if order_item and order_item.product:
                        # Increment product stock
                        order_item.product.stock_quantity += ri.quantity
                        # Un-tick 'is_sold' checkbox
                        order_item.product.is_sold = False
                        # Save the product changes
                        order_item.product.save()
                        logger.info(f"Product '{order_item.product.name}' (ID: {order_item.product.id}) stock updated to {order_item.product.stock_quantity}, is_sold set to False due to return approval.")
                        total_refund_amount += ri.quantity * order_item.price_at_purchase
                    else:
                        logger.warning(f"Product for ReturnItem {ri.id} in ReturnRequest {return_request.id} not found during return approval stock update.")
                
                if total_refund_amount > 0:
                    wallet, created = Wallet.objects.get_or_create(user=return_request.user)
                    wallet.balance += total_refund_amount
                    wallet.save()

                    WalletTransaction.objects.create(
                        wallet=wallet,
                        transaction_type='refund',
                        amount=total_refund_amount,
                        reason=f"Refund for Return Request #{return_request.id} (Order #{order.custom_order_id})"
                    )
                    return_request.status = 'Refunded' # Final status after refund
                else:
                    return_request.status = 'Approved' # If no items or amount to refund, just approve

                # Set the main Order status to 'Returned'
                order.status = 'Returned'
                order.save()

                messages.success(request, f'Return Request #{return_request.id} approved and wallet refunded. Order status set to Returned.')
                logger.info(f"Admin approved return request {return_request.id}. Scheduling return processed email.")
                # FIX: Call with .delay() and pass return_request.id and status
                send_return_processed_email.delay(return_request.id, action) # 'action' will be 'approve' or 'reject'

            elif action == 'reject':
                return_request.status = 'Rejected'
                
                # Set the main Order status back to 'Delivered' if it was 'Returned' or 'Pending' return
                # This logic might need refinement based on your exact desired flow for rejected returns.
                # For now, assuming it goes back to 'Delivered' if it was marked returned for processing.
                if order.status == 'Returned': # Only revert if it was marked returned for processing
                    order.status = 'Delivered'
                    order.save()
                elif order.status == 'Pending': # If it was pending, it should remain delivered
                    order.status = 'Delivered' # Or whatever default state is after processing
                    order.save()


                messages.warning(request, f'Return Request #{return_request.id} rejected. Order status set to Delivered.')
                logger.info(f"Admin rejected return request {return_request.id}. Scheduling return processed email.")
                # FIX: Call with .delay() and pass return_request.id and status
                send_return_processed_email.delay(return_request.id, action) # 'action' will be 'approve' or 'reject'

            return_request.save()

        return JsonResponse({'status': 'success', 'message': f'Return request {action}ed successfully.'})

    except ReturnRequest.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Return request not found.'}, status=404)
    except Exception as e:
        logger.error(f"Error processing admin return request: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': 'An unexpected error occurred.'}, status=500)
    
    
@require_POST
def create_category(request):
    try:
        data = json.loads(request.body)
        category_name = data.get('name').strip()

        if not category_name:
            return JsonResponse({'status': 'error', 'message': 'Category name cannot be empty.'}, status=400)

        # Check if category already exists (case-insensitive)
        if Category.objects.filter(name__iexact=category_name).exists():
            return JsonResponse({'status': 'error', 'message': 'Category with this name already exists.'}, status=409) # 409 Conflict

        category = Category.objects.create(name=category_name)
        return JsonResponse({'status': 'success', 'message': 'Category created successfully!', 'category_id': category.id}, status=201) # 201 Created
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON.'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Server error: {str(e)}'}, status=500)


def get_categories(request):
    categories = Category.objects.all().values('id', 'name') # Get ID and name
    return JsonResponse({'categories': list(categories)})

@require_POST
def create_manual_product(request):
    try:
        name = request.POST.get('name')
        category_id = request.POST.get('category')
        price = request.POST.get('price')
        size = request.POST.get('size', '') # Optional
        description = request.POST.get('description', '') # Optional
        images = request.FILES.getlist('images')

        if not all([name, category_id, price]):
            return JsonResponse({'status': 'error', 'message': 'Name, Category, and Price are required.'}, status=400)

        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Invalid category selected.'}, status=400)

        # Create the product
        product = Product.objects.create(
            name=name,
            category=category,
            price=float(price), # Ensure price is converted to appropriate type
            size=size,
            description=description
        )

        # Save images for the product
        for img_file in images:
            ProductImage.objects.create(product=product, image=img_file)

        return JsonResponse({'status': 'success', 'message': 'Product added manually successfully!'})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Server error: {str(e)}'}, status=500)



@require_POST
def admin_update_order_status(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    new_status = request.POST.get('status')
    if new_status in dict(Order.STATUS_CHOICES):
        order.status = new_status
        order.save()
        messages.success(request, f"Order #{order.id} status updated to {new_status}.")
    else:
        messages.error(request, "Invalid status selected.")
    return redirect('admin_order_list')  # Adjust based on your URL name

def is_superuser(user): # This function seems unused, consider removing if not needed.
    return user.is_authenticated and user.is_superuser


def admin_login_view(request):
    list(messages.get_messages(request))  # Clear previous messages

    if request.user.is_authenticated and request.user.is_superuser:
        messages.info(request, "You are already logged in as an administrator.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user and user.is_superuser:
            login(request, user) # This handles setting the session cookie automatically

            # Set admin session flag and save session (these are for your custom logic, keep them)
            request.session['_auth_admin_id'] = user.pk
            request.session.save()

            request._is_admin_session = True  # for middleware tracking

            # --- REMOVE THE ENTIRE response.set_cookie BLOCK BELOW ---
            # You do NOT need to manually set the session cookie here.
            # Django's login() function and SessionMiddleware handle it.
            # response = redirect('admin_dashboard') # This line also becomes redundant here.
            # response.set_cookie(
            #     settings.ADMIN_SESSION_COOKIE_NAME,  # This setting no longer exists
            #     request.session.session_key,
            #     max_age=settings.SESSION_COOKIE_AGE,
            #     httponly=True,
            #     secure=settings.SESSION_COOKIE_SECURE,
            #     samesite=getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax'),
            # )
            # --- END REMOVE BLOCK ---

            messages.success(request, f"Successfully signed in as administrator: {user.username}.")
            return redirect('admin_dashboard') # Directly redirect after login

        else:
            messages.error(request, "Invalid credentials or not an administrator.")

    return render(request, 'admin_panel/admin_login.html')


@admin_login_required
def admin_logout_view(request):
    # CRITICAL: This logout is ONLY for the admin side.
    # We are explicitly removing our custom admin session key.
    if '_auth_admin_id' in request.session:
        del request.session['_auth_admin_id']

    # Then, flush the entire session data for the current (admin) session.
    # This ensures the 'admin_sessionid' cookie's data is cleared from the database.
    request.session.flush()

    messages.info(request, "You have been logged out from the administrator panel.")
    return redirect('admin_login')


@admin_login_required
def admin_dashboard_view(request):
    filter_option = request.GET.get("range", "all")
    now = timezone.now()
    all_orders = Order.objects.all()

    if filter_option == "month":
        all_orders = all_orders.filter(created_at__year=now.year, created_at__month=now.month)
    elif filter_option == "week":
        start_week = now.date() - timedelta(days=now.weekday())
        all_orders = all_orders.filter(created_at__gte=start_week)

    total_orders = all_orders.count()
    total_pending = all_orders.filter(status='Pending').count()
    delivered_orders = all_orders.filter(status='Delivered')
    total_sales = delivered_orders.aggregate(Sum('total_price'))['total_price__sum'] or 0
    total_customers = User.objects.filter(is_superuser=False, is_active=True).count()

    income_data = defaultdict(Decimal)
    for order in delivered_orders:
        if order.created_at:
            month_label = order.created_at.strftime('%b %Y')
            income_data[month_label] += order.total_price

    sorted_data = sorted(income_data.items(), key=lambda x: timezone.datetime.strptime(x[0], "%b %Y"))
    labels = [item[0] for item in sorted_data]
    values = [float(item[1]) for item in sorted_data]

    return render(request, 'admin_panel/dashboard_home.html', {
        'labels': labels,
        'values': values,
        'total_orders': total_orders,
        'total_sales': total_sales,
        'total_customers': total_customers,
        'total_pending': total_pending
    })

@admin_login_required
@require_POST
def admin_manual_upload(request):
    name = request.POST.get('name')
    cat_id = request.POST.get('category')
    price = request.POST.get('price')
    size = request.POST.get('size')
    desc = request.POST.get('description')
    img = request.FILES.get('image')

    if not (name and cat_id and price and img):
        return JsonResponse({'status': 'error', 'message': 'All fields are required.'})

    category = get_object_or_404(Category, id=cat_id)
    product = Product.objects.create(
        name=name, category=category,
        price=price, size=size, description=desc,
        is_sold=False,
    )
    ProductImage.objects.create(product=product, image=img)
    return JsonResponse({'status': 'success'})


@admin_login_required
def coupon_list(request):
    coupons = Coupon.objects.all().order_by('-id')
    form_create = CouponForm()
    return render(request, 'admin_panel/coupons.html', {'coupons': coupons, 'form_create': form_create})


@require_POST
@admin_login_required
def coupon_create(request):
    form = CouponForm(request.POST)
    if form.is_valid():
        code = form.cleaned_data['code']
        if Coupon.objects.filter(code=code).exists():
            messages.error(request, f"Coupon code '{code}' already exists.")
        else:
            form.save()
            messages.success(request, "Coupon created successfully.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
    return redirect('admin_coupon_list')


@admin_login_required
def edit_coupon_view(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)
    if request.method == 'POST':
        form = CouponForm(request.POST, instance=coupon)
        if form.is_valid():
            form.save()
            messages.success(request, "Coupon updated successfully.")
            return redirect('admin_coupon_list')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
    else:
        form = CouponForm(instance=coupon)
    return render(request, 'admin_panel/edit_coupon.html', {'form': form, 'coupon': coupon})


@require_POST
@admin_login_required
def delete_coupon_view(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)
    coupon_code = coupon.code
    coupon.delete()
    messages.success(request, f"Coupon '{coupon_code}' deleted successfully.")
    return redirect('admin_coupon_list')


@admin_login_required
def admin_order_list(request):
    orders = Order.objects.all().order_by('-created_at')
    return render(request, 'admin_panel/order_list.html', {'orders': orders})


@admin_login_required
def admin_order_detail_view(request, order_id):
    order = get_object_or_404(Order.objects.select_related('user', 'address').prefetch_related('items__product'), id=order_id)
    return render(request, 'admin_panel/order_detail.html', {
        'order': order,
        'customer': order.user,
        'address': order.address,
        'order_items': order.items.all(),
    })


@admin_login_required
def admin_products_view(request):
    products = Product.objects.all().prefetch_related('images')
    categories = Category.objects.all()  # ✅ For dropdown filter

    # ✅ Get filters
    status = request.GET.get('status')
    date_filter = request.GET.get('date')
    search_query = request.GET.get('q')
    category_id = request.GET.get('category')

    # ✅ Filter by availability
    if status == 'available':
        products = products.filter(is_sold=False)
    elif status == 'sold':
        products = products.filter(is_sold=True)

    # ✅ Filter by date
    if date_filter in ['new', 'old']:
        cutoff = timezone.now() - timedelta(days=15)
        if date_filter == 'new':
            products = products.filter(created_at__gte=cutoff)
        elif date_filter == 'old':
            products = products.filter(created_at__lt=cutoff)

    # ✅ Search by product name
    if search_query:
        products = products.filter(name__icontains=search_query)

    # ✅ Filter by category
    if category_id:
        products = products.filter(category_id=category_id)

    products = products.order_by('-id')
    return render(request, 'admin_panel/product_list.html', {
        'products': products,
        'categories': categories  # ✅ Send categories to template
    })

@admin_login_required
def edit_product_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            return redirect('admin_product_list')
    else:
        form = ProductForm(instance=product)

    if request.GET.get('modal') == 'true':
        return render(request, 'admin_panel/partials/edit_form_fields.html', {'form': form})

    return render(request, 'admin_panel/edit_product.html', {'form': form, 'product': product})


@admin_login_required
def delete_product_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.delete()
        return redirect('admin_product_list')
    return render(request, 'admin_panel/product_confirm_delete.html', {'product': product})


@admin_login_required
def batch_upload_view(request):
    if request.method == 'POST':
        product_file = request.FILES.get('product_file')
        images = request.FILES.getlist('images')

        if not product_file or not images:
            return JsonResponse({'status': 'error', 'message': 'Please upload both a product file and product images.'}, status=400)

        try:
            content = product_file.read().decode('utf-8')
            blocks = content.strip().split("\n\n")
            products_to_create = []

            for block in blocks:
                lines = [line.strip() for line in block.strip().split('\n') if line.strip()]

                product_info = {}
                description_lines = []
                in_description_mode = False

                for line in lines:
                    if line.lower().startswith('name:'):
                        product_info['name'] = line.split(':', 1)[1].strip()
                        in_description_mode = False
                    elif line.lower().startswith('category:'):
                        product_info['category'] = line.split(':', 1)[1].strip()
                        in_description_mode = False
                    elif line.lower().startswith('price:'):
                        try:
                            product_info['price'] = float(line.split(':', 1)[1].strip())
                        except ValueError:
                            print(f"Warning: Invalid price format in block: {block}")
                            product_info['price'] = 0.0
                        in_description_mode = False
                    elif line.lower().startswith('size:'):
                        product_info['size'] = line.split(':', 1)[1].strip()
                        in_description_mode = False
                    elif line.lower().startswith('description:'):
                        description_lines.append(line.split(':', 1)[1].strip())
                        in_description_mode = True
                    elif in_description_mode:
                        description_lines.append(line)

                if all(key in product_info for key in ['name', 'category', 'price', 'size']) and description_lines:
                    product_info['description'] = "\n".join(description_lines)
                    products_to_create.append(product_info)
                else:
                    print(f"Skipping malformed or incomplete product block: \n{block}")

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Error parsing product file: {e}'}, status=400)

        total_products = len(products_to_create)
        total_images = len(images)
        expected_image_count = total_products * 3

        if total_products == 0:
             return JsonResponse({'status': 'error', 'message': 'No valid products found in the file. Please check the format and ensure products are separated by two newlines.'}, status=400)

        if total_products > 30 or total_images > 90:
            return JsonResponse({'status': 'error', 'message': 'Upload limit is 30 products and 90 images at a time.'}, status=400)

        if total_images != expected_image_count:
            return JsonResponse({
                'status': 'error',
                'message': f'Expected {expected_image_count} images (3 per product), but got {total_images}.'
            }, status=400)

        image_index = 0
        for product_info in products_to_create:
            category_name = product_info.pop('category')
            category_obj, _ = Category.objects.get_or_create(name__iexact=category_name, defaults={'name': category_name})
            product = Product.objects.create(**product_info, category=category_obj)

            for i in range(3):
                if image_index < len(images):
                    ProductImage.objects.create(
                        product=product,
                        image=images[image_index],
                        order=i
                    )
                    image_index += 1

        return JsonResponse({'status': 'success', 'message': f'{total_products} products have been uploaded successfully!'})

    return render(request, 'admin_panel/upload.html')

def admin_sales_view(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    orders = Order.objects.all()
    if start_date and end_date:
        orders = orders.filter(created_at__date__range=[start_date, end_date])

    total_sales = orders.aggregate(total=Sum('total_price'))['total'] or 0
    total_orders = orders.count()
    total_products_sold = (
        OrderItem.objects
        .filter(order__in=orders)
        .aggregate(qty=Sum('quantity'))['qty'] or 0
    )

    # Daily Sales Chart
    daily = (
        orders
        .annotate(day=TruncDay('created_at'))
        .values('day')
        .annotate(total=Sum('total_price'))
        .order_by('day')
    )
    daily_labels = [x['day'].strftime('%d %b') for x in daily]
    daily_data = [float(x['total']) for x in daily]  #  Convert Decimal to float

    # Monthly Sales Chart
    monthly = (
        orders
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(total=Sum('total_price'))
        .order_by('month')
    )
    monthly_labels = [x['month'].strftime('%b %Y') for x in monthly]
    monthly_data = [float(x['total']) for x in monthly]  #  Convert Decimal to float

    # Top Products
    top_products = (
        OrderItem.objects
        .filter(order__in=orders)
        .values('product__name')
        .annotate(total_sold=Sum('quantity'))
        .order_by('-total_sold')[:5]
    )

    # Revenue by Category (Pie Chart)
    category_sales = (
        OrderItem.objects
        .filter(order__in=orders)
        .values('product__category__name')
        .annotate(total=Sum('price_at_purchase'))
    )
    category_labels = [c['product__category__name'] for c in category_sales]
    category_data = [float(c['total']) for c in category_sales]  #  Decimal to float

    # Payment Breakdown (COD, Razorpay)
    # Assuming you store method in payment_status or elsewhere — update this field
    payment_data_qs = (
        orders
        .values('payment_method')
        .annotate(total=Sum('total_price'))
    )

    payment_labels = [p['payment_method'] or 'Unknown' for p in payment_data_qs]
    payment_totals = [float(p['total']) for p in payment_data_qs]

    context = {
        'orders': orders,
        'total_sales': total_sales,
        'total_orders': total_orders,
        'total_products_sold': total_products_sold,
        'top_products': top_products,
        'start_date': start_date,
        'end_date': end_date,
        'daily_labels': json.dumps(daily_labels),
        'daily_data': json.dumps(daily_data),
        'monthly_labels': json.dumps(monthly_labels),
        'monthly_data': json.dumps(monthly_data),
        'category_labels': json.dumps(category_labels),
        'category_data': json.dumps(category_data),
        'payment_labels': json.dumps(payment_labels),
        'payment_data': json.dumps(payment_totals),
    }

    return render(request, 'admin_panel/sales_report.html', context)

@admin_login_required
def admin_user_list(request):
    users = User.objects.filter(is_superuser=False)
    return render(request, 'admin_panel/user_list.html', {'users': users})


@require_POST
@admin_login_required
def toggle_user_status(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if request.user.pk != user.pk:
        user.is_active = not user.is_active
        user.save()
        messages.success(request, f"User {user.username} status toggled successfully.")
    else:
        messages.error(request, "You cannot change your own active status.")
    return redirect('admin_user_list')


def export_sales_excel_view(request):
    orders = Order.objects.select_related('user').prefetch_related('items').all()

    data = []
    for order in orders:
        for item in order.items.all():
            data.append({
                'Order ID': f"#{order.id}",
                'Customer': order.user.email,
                'Product': item.product.name,
                'Quantity': item.quantity,
                'Price': item.price_at_purchase,
                'Total': item.quantity * item.price_at_purchase,
                'Date': order.created_at.strftime('%Y-%m-%d'),
                'Payment Method': order.payment_method,
            })

    df = pd.DataFrame(data)
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=sales_report.xlsx'
    df.to_excel(response, index=False)

    return response

# Export to PDF
def export_sales_pdf(request):
    buffer = BytesIO()
    p = canvas.Canvas(buffer)

    orders = Order.objects.all()
    y = 800
    p.setFont("Helvetica-Bold", 14)
    p.drawString(200, y, "Sales Report")
    y -= 40

    p.setFont("Helvetica", 10)
    for order in orders:
        p.drawString(40, y, f"Order: #{order.id}, Email: {order.user.email}, ₹{order.total_price}, {order.created_at.strftime('%Y-%m-%d')}")
        y -= 20
        if y < 40:
            p.showPage()
            y = 800

    p.save()
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="sales_report.pdf"'
    return response

@method_decorator(admin_login_required, name='dispatch')
class EmailTemplateListView(ListView):
    model = EmailTemplate
    template_name = 'admin_panel/newsletter/template_list.html'
    context_object_name = 'templates'

@method_decorator(xframe_options_exempt, name='dispatch')
class EmailTemplateCreateView(CreateView):
    model = EmailTemplate
    form_class = EmailTemplateForm
    template_name = 'admin_panel/newsletter/template_form.html'
    success_url = reverse_lazy('admin_email_template_list') # This will now only be used for non-modal submits

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        email_template = form.save() # Save the form and get the instance

        # Check if this request came from the modal context.
        # We can detect this by checking if 'modal=true' is in the GET parameters
        # of the request that initially loaded this form, and assume the POST
        # submission follows the same context.
        # Alternatively, you could pass a hidden input field 'is_modal_submit' in the form.
        is_modal_request = 'modal' in self.request.GET

        if is_modal_request:
            # If it's a modal submission, we do NOT want to redirect the iframe.
            # Instead, send a signal back to the parent.
            # Your template already has: window.parent.postMessage("template_created", "*");
            # We just need to make sure the view finishes successfully without redirecting
            # the iframe to an invalid URL.
            
            # Option 1: Return a simple HttpResponse (preferred for iframes)
            # This HTML will be rendered inside the iframe, but it's very minimal.
            # The JS will execute and send the message, and then this small page just sits there.
            # It's crucial for the JS to run AFTER the form is valid.
            return HttpResponse(
                "<script>window.parent.postMessage('template_created', '*'); window.location.href = '';</script>",
                status=200 # Indicate success
            )
            
            # Option 2: Redirect to the *edit* view of the newly created template within the modal
            # This keeps the modal open but shows the saved template ready for further edits.
            # Ensure your JS for postMessage also handles this redirect for closing if needed.
            # return redirect(reverse('newsletter:create_email_template') + f'?modal=true&pk={email_template.pk}')


        # If it's not a modal (e.g., direct access to this form view),
        # then proceed with the default CreateView behavior (redirect to success_url).
        return super().form_valid(form) # This will trigger the redirect to success_url

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Pass a 'modal' flag to the template if it's detected in the GET parameters
        if 'modal' in self.request.GET:
            context['modal'] = True
            # If editing existing template within modal (optional, but good practice)
            pk = self.request.GET.get('pk')
            if pk:
                try:
                    email_template = self.model.objects.get(pk=pk)
                    context['form'] = self.form_class(instance=email_template)
                except self.model.DoesNotExist:
                    pass # Handle error or just show empty form
        return context

    # If you also want to handle editing existing templates with this same view in the modal:
    # def get_object(self, queryset=None):
    #     pk = self.request.GET.get('pk')
    #     if pk:
    #         return self.model.objects.get(pk=pk)
    #     return super().get_object(queryset)

@method_decorator(admin_login_required, name='dispatch')
class EmailTemplateUpdateView(UpdateView):
    model = EmailTemplate
    form_class = EmailTemplateForm
    template_name = 'admin_panel/newsletter/template_form.html'
    success_url = reverse_lazy('admin_email_template_list')

@method_decorator(admin_login_required, name='dispatch')
class EmailTemplateDeleteView(DeleteView):
    model = EmailTemplate
    template_name = 'admin_panel/newsletter/template_confirm_delete.html'
    success_url = reverse_lazy('admin_email_template_list')

@admin_login_required
def email_template_preview(request, pk):
    template_obj = get_object_or_404(EmailTemplate, pk=pk)
    context = {
        'user': request.user,
        'site_name': 'Wearin',
        'site_url': 'http://127.0.0.1:8000',
        'subject_var': template_obj.subject,
    }
    try:
        rendered_html = Template(template_obj.html_content).render(Context(context))
    except Exception as e:
        rendered_html = f"<p style='color:red;'>Error rendering template: {e}</p>"
    return render(request, 'admin_panel/newsletter/template_preview.html', {'rendered_html': rendered_html})

class NewsletterCampaignCreateView(CreateView):
    model = NewsletterCampaign
    form_class = NewsletterCampaignForm
    template_name = 'admin_panel/newsletter/campaign_form.html'
    success_url = reverse_lazy('admin_newsletter_campaign_list')

    def form_valid(self, form):
        if not form.instance.sent_by:
            form.instance.sent_by = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from core.models import EmailTemplate  # or wherever your model is
        context['templates'] = EmailTemplate.objects.all()
        return context
    
@method_decorator(admin_login_required, name='dispatch')
class NewsletterCampaignListView(ListView):
    model = NewsletterCampaign
    template_name = 'admin_panel/newsletter/campaign_list.html'
    context_object_name = 'campaigns'

@method_decorator(admin_login_required, name='dispatch')
class NewsletterCampaignUpdateView(UpdateView):
    model = NewsletterCampaign
    fields = ['title', 'email_template', 'scheduled_at', 'status']
    template_name = 'admin_panel/newsletter/campaign_edit.html'
    success_url = reverse_lazy('admin_newsletter_campaign_list')

@method_decorator(admin_login_required, name='dispatch')
class NewsletterCampaignDeleteView(DeleteView):
    model = NewsletterCampaign
    template_name = 'admin_panel/newsletter/campaign_confirm_delete.html'
    success_url = reverse_lazy('admin_newsletter_campaign_list')


@admin_login_required
def send_newsletter_campaign_view(request, pk):
    campaign = get_object_or_404(NewsletterCampaign, pk=pk)

    try:
        send_newsletter_to_subscribers(campaign)  # you need this logic in core/utils.py
        campaign.is_sent = True
        campaign.save()
        messages.success(request, "Newsletter sent successfully.")
    except Exception as e:
        messages.error(request, f"Error sending newsletter: {str(e)}")

    return redirect('admin_newsletter_campaign_list')

@admin_login_required
def ajax_create_email_template(request):
    if request.method == "POST":
        form = EmailTemplateForm(request.POST)
        if form.is_valid():
            template = form.save()
            return JsonResponse({
                'success': True,
                'id': template.id,
                'name': template.name,
                'html': render_to_string('admin_panel/newsletter/partials/template_card.html', {'template': template})
            })
        return JsonResponse({'success': False, 'errors': form.errors})