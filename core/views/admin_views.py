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
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from core.decorators import admin_login_required
from core.models import Order, OrderItem, Product, Address, Category, ProductImage, Coupon
from core.forms import ProductForm, CouponForm
from django.db.models import Count
from django.utils.timezone import now
from django.db.models.functions import TruncDate
from datetime import datetime
import pandas as pd
from io import BytesIO
from django.http import HttpResponse
from django.db.models.functions import TruncDay, TruncMonth
from reportlab.pdfgen import canvas
from core.models import Order, WalletTransaction, Wallet


@admin_login_required
def approve_return_view(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    if order.return_status != 'Requested':
        messages.error(request, 'Invalid return state.')
        return redirect('admin_order_list')

    order.return_status = 'Approved'
    order.status = 'Returned'

    # Refund to wallet
    wallet, _ = Wallet.objects.get_or_create(user=order.user)
    wallet.credit(order.total_price)
    WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type='refund',
        amount=order.total_price,
        reason=f"Refund for returned Order #{order.id}"
    )
    order.save()

    messages.success(request, 'Return approved and wallet refunded.')
    return redirect('admin_order_list')

@admin_login_required
def reject_return_view(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    if order.return_status != 'Requested':
        messages.error(request, 'Invalid return state.')
        return redirect('admin_order_list')

    order.return_status = 'Rejected'
    order.save()

    messages.warning(request, 'Return request rejected.')
    return redirect('admin_order_list')

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
            login(request, user)

            # Set admin session flag and save session
            request.session['_auth_admin_id'] = user.pk
            request.session.save()

            request._is_admin_session = True  # for middleware tracking

            # Manually create response to set cookie
            response = redirect('admin_dashboard')

            response.set_cookie(
                settings.ADMIN_SESSION_COOKIE_NAME,  # typically 'admin_sessionid'
                request.session.session_key,
                max_age=settings.SESSION_COOKIE_AGE,
                httponly=True,
                secure=settings.SESSION_COOKIE_SECURE,
                samesite=getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax'),
            )

            messages.success(request, f"Successfully signed in as administrator: {user.username}.")
            return response

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
    payment_data = (
        orders
        .values('payment_status')  #  Use actual field available in your model
        .annotate(total=Sum('total_price'))
    )
    payment_labels = [p['payment_status'] for p in payment_data]
    payment_totals = [float(p['total']) for p in payment_data]  #  Decimal to float

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