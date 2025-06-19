from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout as auth_logout
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import JsonResponse # Import JsonResponse
from core.forms import UserLoginForm, UserRegistrationForm
from core.models import Product, Category, Wishlist # Import Wishlist model
from django.urls import reverse
from core.forms import ProfileForm, AddressForm
from core.models import Order, UserProfile

@login_required
def update_profile(request):
    if request.method == 'POST':
        user = request.user
        user.first_name = request.POST.get('first_name')
        user.last_name = request.POST.get('last_name')
        user.email = request.POST.get('email')
        user.save()
        messages.success(request, "Profile updated successfully!")
        return redirect('my_profile')

@login_required
def update_address(request):
    if request.method == 'POST':
        profile = request.user.profile  # or however you store the address
        profile.address = request.POST.get('address')
        profile.city = request.POST.get('city')
        profile.state = request.POST.get('state')
        profile.pincode = request.POST.get('pincode')
        profile.save()
        messages.success(request, "Address updated successfully!")
        return redirect('my_profile')

@login_required
def profile_view(request):
    user = request.user

    # Handle OneToOne profile
    user_profile, created = UserProfile.objects.get_or_create(user=user)

    profile_form = ProfileForm(instance=user)
    address_form = AddressForm(instance=user_profile)
    orders = Order.objects.filter(user=user).order_by('-created_at')

    if request.method == 'POST':
        if 'name' in request.POST:
            profile_form = ProfileForm(request.POST, instance=user)
            if profile_form.is_valid():
                profile_form.save()
                return redirect('profile_view')
        elif 'street' in request.POST:  # a key from the address form
            address_form = AddressForm(request.POST, instance=user_profile)
            if address_form.is_valid():
                address_form.save()
                return redirect('profile_view')

    return render(request, 'user/profile.html', {
        'profile_form': profile_form,
        'address_form': address_form,
        'user_address': user_profile,  # So template knows address exists
        'orders': orders
    })

    
@login_required
def my_profile(request):
    return render(request, 'user/main/profile.html')
@login_required
def my_address(request):
    return render(request, 'user/profile/my_address.html')

@login_required
def my_orders(request):
    orders = request.user.order_set.all()  # or your Order model
    return render(request, 'user/profile/my_orders.html', {'orders': orders})

# --- Auth Views ---

def login_view(request):
    if request.user.is_authenticated:
        return redirect('user_dashboard')

    if request.method == 'POST':
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, {user.username}!")
                return redirect('user_dashboard')
            else:
                messages.error(request, "Invalid username or password.")
    else:
        form = UserLoginForm()

    return render(request, 'user/auth/login.html', {'form': form})


def register_view(request):
    if request.user.is_authenticated:
        return redirect('user_dashboard')

    if request.method == 'POST':
        form = UserRegistrationForm(data=request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Account created successfully! Welcome to WEARIN.")
            return redirect('user_dashboard')
    else:
        form = UserRegistrationForm()

    return render(request, 'user/auth/register.html', {'form': form})


def forgot_password_view(request):
    return render(request, 'user/auth/forgot_password.html')


def confirm_code_view(request):
    return render(request, 'user/auth/confirm_code.html')


def reset_password_view(request):
    return render(request, 'user/auth/reset_password.html')


def logout_view(request):
    auth_logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('home')


# --- Main Pages ---

def home_view(request):
    new_products = Product.objects.filter(is_sold=False).order_by('-created_at')[:12]
    categories = Category.objects.all()

    wishlisted_product_ids = []
    if request.user.is_authenticated:
        wishlisted_product_ids = Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True)

    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    return render(request, 'user/main/home.html', {
        'new_products': new_products,
        'categories': categories,
        'wishlisted_product_ids': list(wishlisted_product_ids),
        'cart_count': cart_count,
    })



@login_required
def user_dashboard_view(request):
    return render(request, 'user/main/authenticated_home.html')


def shop_view(request):
    products = Product.objects.filter(is_sold=False).order_by('-created_at')
    categories = Category.objects.all()

    wishlisted_product_ids = []
    if request.user.is_authenticated:
        wishlisted_product_ids = Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True)

    return render(request, 'user/main/shop.html', {
        'products': products,
        'categories': categories,
        'wishlisted_product_ids': list(wishlisted_product_ids), # Convert to list for template
    })


def product_detail_view(request, id=None):
    product = None
    if id:
        product = get_object_or_404(Product, id=id)
    return render(request, 'user/main/product_detail.html', {'product': product})


# --- Static Pages ---

def policy_view(request):
    return render(request, 'user/main/static_pages/policy.html')

def contact_view(request):
    return render(request, 'user/main/static_pages/contact.html')

def about_view(request):
    return render(request, 'user/main/static_pages/about.html')


# --- CART SYSTEM (Session Based) ---

@login_required
def cart_page_view(request):
    cart = request.session.get('cart', {})
    cart_items = []
    total_price = 0

    for product_id, item in cart.items():
        # Ensure product still exists in database, handle if it's been deleted
        try:
            product_obj = Product.objects.get(id=int(product_id))
            item_total = item['price'] * item['quantity']
            item['total'] = item_total
            item['product_obj'] = product_obj # Add product object for detailed info if needed
            cart_items.append((product_id, item))
            total_price += item_total
        except Product.DoesNotExist:
            # Optionally remove item from session cart if product no longer exists
            if product_id in request.session['cart']:
                del request.session['cart'][product_id]
                request.session.modified = True # Mark session as modified
            messages.warning(request, f"Product (ID: {product_id}) not found and removed from your cart.")


    context = {
        'cart_items': cart_items,
        'total_price': total_price,
    }
    return render(request, 'user/main/cart.html', context)


@login_required
def add_to_cart_view(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    cart = request.session.get('cart', {})

    product_id_str = str(product_id)
    if product_id_str in cart:
        cart[product_id_str]['quantity'] += 1
        messages.info(request, f"Increased quantity of {product.name} in your cart.")
    else:
        cart[product_id_str] = {
            'name': product.name,
            'price': float(product.price),
            'quantity': 1,
            'image_url': product.images.first().image.url if product.images.exists() else '',
        }
        messages.success(request, f"{product.name} added to your cart.")


    request.session['cart'] = cart
    return redirect('cart_page')


@require_POST
@login_required
def remove_from_cart_view(request, product_id):
    cart = request.session.get('cart', {})
    product_id_str = str(product_id)

    if product_id_str in cart:
        product_name = cart[product_id_str]['name'] # Get name before deleting
        del cart[product_id_str]
        request.session['cart'] = cart
        messages.info(request, f"{product_name} removed from your cart.")
    else:
        messages.warning(request, "Product not found in your cart.")


    return redirect('cart_page')


@require_POST
@login_required
def buy_now_view(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    request.session['buy_now'] = {
        'id': product.id,
        'name': product.name,
        'price': float(product.price),
        'image': product.images.first().image.url if product.images.exists() else None,
    }
    return redirect('checkout_page')


# --- WISHLIST SYSTEM ---

@login_required
def wishlist_view(request):
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__category') # Optimise query
    context = {
        'wishlist_items': wishlist_items
    }
    return render(request, 'user/main/wishlist.html', context)

@login_required
@require_POST # Ensure this view only accepts POST requests
def toggle_wishlist(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
        # If not an AJAX request, redirect or return error
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

@login_required
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