from django.db.models import Sum
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.admin.views.decorators import staff_member_required
from collections import defaultdict
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from datetime import timedelta
from core.models import (
    Order, OrderItem, Product, Address, Category, ProductImage, Coupon
)
from core.forms import ProductForm, CouponForm
from django.contrib import messages

@staff_member_required
def coupon_list(request):
    coupons = Coupon.objects.all().order_by('-id')
    form_create = CouponForm()
    return render(request, 'admin_panel/coupons.html', {'coupons': coupons, 'form_create': form_create})

@require_POST
@staff_member_required
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

@staff_member_required
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
@staff_member_required
def delete_coupon_view(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)
    coupon_code = coupon.code
    coupon.delete()
    messages.success(request, f"Coupon '{coupon_code}' deleted successfully.")
    return redirect('admin_coupon_list')

@staff_member_required
def admin_order_list(request):
    orders = Order.objects.all().order_by('-created_at')
    return render(request, 'admin_panel/order_list.html', {'orders': orders})

@staff_member_required
def admin_order_detail_view(request, order_id):
    order = get_object_or_404(Order.objects.select_related('user', 'address').prefetch_related('items__product'), id=order_id)
    context = {
        'order': order,
        'customer': order.user,
        'address': order.address,
        'order_items': order.items.all(),
    }
    return render(request, 'admin_panel/order_detail.html', context)

@staff_member_required
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

@require_POST
@staff_member_required
def toggle_user_status(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if request.user != user:
        user.is_active = not user.is_active
        user.save()
    return redirect('admin_user_list')

@staff_member_required
def admin_user_list(request):
    users = User.objects.filter(is_superuser=False)
    return render(request, 'admin_panel/user_list.html', {'users': users})

@staff_member_required
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

@staff_member_required
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

@staff_member_required
def delete_product_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.delete()
        return redirect('admin_product_list')
    return render(request, 'admin_panel/product_confirm_delete.html', {'product': product})

@staff_member_required
def batch_upload_view(request):
    if request.method == 'POST':
        product_file = request.FILES.get('product_file')
        images = request.FILES.getlist('images')

        if not product_file or not images:
            return JsonResponse({'status': 'error', 'message': 'Please upload both a product file and product images.'}, status=400)

        try:
            content = product_file.read().decode('utf-8')
            blocks = content.strip().split("\n\n")
            products = []

            for block in blocks:
                lines = [line.strip() for line in block.strip().split('\n')]
                if len(lines) != 5:
                    continue
                try:
                    product = {
                        'name': lines[0].split(':', 1)[1].strip(),
                        'category': lines[1].split(':', 1)[1].strip(),
                        'price': float(lines[2].split(':', 1)[1].strip()),
                        'size': lines[3].split(':', 1)[1].strip(),
                        'description': lines[4].split(':', 1)[1].strip(),
                    }
                    products.append(product)
                except (IndexError, ValueError):
                    continue
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Error parsing product file: {e}'}, status=400)

        total_products = len(products)
        total_images = len(images)
        expected_image_count = total_products * 3

        if total_products > 30 or total_images > 90:
            return JsonResponse({'status': 'error', 'message': 'Upload limit is 30 products and 90 images at a time.'}, status=400)

        if total_images != expected_image_count:
            return JsonResponse({
                'status': 'error',
                'message': f'Expected {expected_image_count} images (3 per product), but got {total_images}.'
            }, status=400)

        image_index = 0
        for product_info in products:
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
