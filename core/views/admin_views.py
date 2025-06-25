from django.db.models import Sum
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
# from django.contrib.admin.views.decorators import staff_member_required # No longer needed directly
from collections import defaultdict
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth import authenticate # Don't import login/logout from django.contrib.auth here, we'll manage sessions manually
from django.contrib.auth import get_user_model # To get User model
# from django.contrib.auth.decorators import user_passes_test # No longer needed directly

# Import our custom decorators
from core.decorators import admin_login_required, user_login_required

from core.models import (
    Order, OrderItem, Product, Address, Category, ProductImage, Coupon
)
from core.forms import ProductForm, CouponForm


# Custom Superuser Check - No longer directly used as a decorator, but still useful for logic
def is_superuser(user):
    return user.is_authenticated and user.is_superuser


def admin_login_view(request):
    # If already logged in as admin via the admin portal (check custom key)
    if request.session.get('_auth_user_id_admin_portal'):
        messages.info(request, "You are already logged in as an admin.")
        return redirect('admin_dashboard')

    # If currently logged in as a regular user (check default Django key)
    # This ensures a regular user can't access the admin login page
    if request.session.get('_auth_user_id') and not request.user.is_superuser:
        messages.warning(request, "You are currently logged in as a user. Please log out of your user account if you wish to log in as an admin.")
        return redirect('user_dashboard') # Or wherever your user dashboard is

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user and user.is_superuser:
            # Manually set the admin-specific session key
            request.session['_auth_user_id_admin_portal'] = user.pk
            # Optionally, clear any existing regular user session for this user to prevent confusion
            if '_auth_user_id' in request.session:
                del request.session['_auth_user_id']
            
            messages.success(request, f"Successfully signed in as admin: {user.username}.")
            return redirect('admin_dashboard')
        else:
            messages.error(request, "Invalid credentials or not an administrator.")

    return render(request, 'admin_panel/admin_login.html')


# Replace user_passes_test with our custom decorator
@admin_login_required
def admin_logout_view(request):
    # Clear only the admin-specific session key
    if '_auth_user_id_admin_portal' in request.session:
        del request.session['_auth_user_id_admin_portal']
        # Also clear the generic Django session. This is important.
        # If the superuser was also logged in as a 'user', this logs them out of both
        # in the context of this specific browser tab/session being used for admin.
        # To maintain user login, the user would need to log in separately on the user side.
        request.session.flush() # Clear the entire session for this browser if logging out of admin
                                # This ensures no leftover user auth.
    messages.info(request, "You have been logged out from the admin panel.")
    return redirect('admin_login')


# Dashboard View
# Apply the new admin_login_required decorator
@admin_login_required
def admin_dashboard_view(request):
    # No need for the request.session.get('admin_logged_in') check here anymore,
    # as the decorator handles access.

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

    # Use actual datetime objects for sorting to ensure correct order
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


# Apply admin_login_required to all admin views
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


# Order Views
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


# Product Views
@admin_login_required
def admin_products_view(request):
    products = Product.objects.all().prefetch_related('images')
    status = request.GET.get('status')
    date_filter = request.GET.get('date')

    if status == 'available':
        products = products.filter(is_sold=False)
    elif status == 'sold':
        products = products.filter(is_sold=True)

    if date_filter == 'new':
        cutoff = timezone.now() - timedelta(days=15)
        products = products.filter(created_at__gte=cutoff)
    elif date_filter == 'old':
        cutoff = timezone.now() - timedelta(days=15)
        products = products.filter(created_at__lt=cutoff)

    products = products.order_by('-id')
    return render(request, 'admin_panel/product_list.html', {'products': products})


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


# Upload Products
@admin_login_required
def batch_upload_view(request):
    if request.method == 'POST':
        product_file = request.FILES.get('product_file')
        images = request.FILES.getlist('images')

        if not product_file or not images:
            return JsonResponse({'status': 'error', 'message': 'Please upload both a product file and product images.'}, status=400)

        try:
            content = product_file.read().decode('utf-8')
            # Split by blocks of content, assuming each product is separated by two newlines
            blocks = content.strip().split("\n\n")
            products_to_create = []

            for block in blocks:
                # Split each block into lines, filter out empty lines to avoid issues with extra newlines
                lines = [line.strip() for line in block.strip().split('\n') if line.strip()]

                product_info = {}
                description_lines = []
                in_description_mode = False # Flag to indicate if we are currently parsing the description

                for line in lines:
                    # Check for known field prefixes
                    if line.lower().startswith('name:'):
                        product_info['name'] = line.split(':', 1)[1].strip()
                        in_description_mode = False # Reset description mode
                    elif line.lower().startswith('category:'):
                        product_info['category'] = line.split(':', 1)[1].strip()
                        in_description_mode = False
                    elif line.lower().startswith('price:'):
                        try:
                            product_info['price'] = float(line.split(':', 1)[1].strip())
                        except ValueError:
                            # Handle cases where price might not be a valid float
                            print(f"Warning: Invalid price format in block: {block}")
                            product_info['price'] = 0.0 # Default or handle as error
                        in_description_mode = False
                    elif line.lower().startswith('size:'):
                        product_info['size'] = line.split(':', 1)[1].strip()
                        in_description_mode = False
                    elif line.lower().startswith('description:'):
                        # Start collecting description. The rest of this line is the first part.
                        description_lines.append(line.split(':', 1)[1].strip())
                        in_description_mode = True
                    elif in_description_mode:
                        # If we are in description mode, append the current line to description_lines
                        description_lines.append(line)
                    # Add more elif for other single-line fields if they exist in your file format

                # After parsing all lines in the block, check if essential fields are present
                if all(key in product_info for key in ['name', 'category', 'price', 'size']) and description_lines:
                    product_info['description'] = "\n".join(description_lines)
                    products_to_create.append(product_info)
                else:
                    # Log or message if a product block is malformed or missing essential data
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
        for product_info in products_to_create: # Use the newly parsed list
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


# User List
@admin_login_required
def admin_user_list(request):
    # Only show non-superuser users
    users = User.objects.filter(is_superuser=False)
    return render(request, 'admin_panel/user_list.html', {'users': users})


# Toggle User Activation
@require_POST
@admin_login_required
def toggle_user_status(request, user_id):
    user = get_object_or_404(User, id=user_id)
    # Ensure the admin can't deactivate themselves
    if request.user.pk != user.pk: 
        user.is_active = not user.is_active
        user.save()
        messages.success(request, f"User {user.username} status toggled successfully.")
    else:
        messages.error(request, "You cannot change your own active status.")
    return redirect('admin_user_list')