from django.shortcuts import render, redirect, get_object_or_404
# from django.contrib.auth.decorators import login_required # No longer using default login_required
from django.contrib.auth import login, authenticate # Don't import logout from django.contrib.auth here
from django.contrib.auth import get_user_model # To get User model
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.urls import reverse

# Import our custom decorators
from core.decorators import admin_login_required, user_login_required

from core.forms import UserLoginForm, UserRegistrationForm, ProfileForm, AddressForm
from core.models import Product, Category, Wishlist, Order, UserProfile


@user_login_required # Apply the new decorator
def update_profile(request):
    if request.method == 'POST':
        user = request.user
        user.first_name = request.POST.get('first_name')
        user.last_name = request.POST.get('last_name')
        user.email = request.POST.get('email')
        user.save()
        messages.success(request, "Profile updated successfully!")
        return redirect('my_profile')


@user_login_required # Apply the new decorator
def update_address(request):
    if request.method == 'POST':
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.address = request.POST.get('address')
        profile.city = request.POST.get('city')
        profile.state = request.POST.get('state')
        profile.pincode = request.POST.get('pincode')
        profile.save()
        messages.success(request, "Address updated successfully!")
        return redirect('my_profile')


@user_login_required # Apply the new decorator
def profile_view(request):
    user = request.user
    user_profile, created = UserProfile.objects.get_or_create(user=user)
    profile_form = ProfileForm(instance=user)
    address_form = AddressForm(instance=user_profile)
    orders = Order.objects.filter(user=user).order_by('-created_at')

    if request.method == 'POST':
        if 'update_profile' in request.POST:
            profile_form = ProfileForm(request.POST, instance=user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Profile updated successfully!")
                return redirect('my_profile')
        elif 'update_address' in request.POST:
            address_form = AddressForm(request.POST, instance=user_profile)
            if address_form.is_valid():
                address_form.save()
                messages.success(request, "Address updated successfully!")
                return redirect('my_profile')
        else:
            messages.error(request, "Invalid form submission.")

    return render(request, 'user/profile.html', {
        'profile_form': profile_form,
        'address_form': address_form,
        'user_address': user_profile,
        'orders': orders
    })


@user_login_required # Apply the new decorator
def my_profile(request):
    user_profile, created = UserProfile.objects.get_or_create(user=request.user)
    profile_form = ProfileForm(instance=request.user)
    address_form = AddressForm(instance=user_profile)
    return render(request, 'user/main/profile.html', {
        'profile_form': profile_form,
        'address_form': address_form,
        'user_address': user_profile
    })


@user_login_required # Apply the new decorator
def my_address(request):
    user_profile, created = UserProfile.objects.get_or_create(user=request.user)
    address_form = AddressForm(instance=user_profile)
    return render(request, 'user/profile/my_address.html', {'address_form': address_form, 'user_address': user_profile})


@user_login_required # Apply the new decorator
def my_orders(request):
    orders = request.user.order_set.all()
    return render(request, 'user/profile/my_orders.html', {'orders': orders})


# --- Auth Views ---

def login_view(request):
    # Check if currently logged in as admin via the admin portal (custom key)
    if request.session.get('_auth_user_id_admin_portal'):
        messages.warning(request, "You are logged in as an admin. Please log out from the admin panel first if you wish to log in as a regular user.")
        return redirect('admin_dashboard')

    # If already logged in as a user (check default Django key)
    if request.session.get('_auth_user_id'):
        messages.info(request, "You are already logged in as a user.")
        return redirect('user_dashboard')

    if request.method == 'POST':
        form = UserLoginForm(request=request, data=request.POST) # Pass request to AuthenticationForm
        if form.is_valid():
            user = form.get_user()
            # Ensure it's not a superuser trying to log into the user side IF you want strict prevention
            # If a superuser can *also* act as a regular user via this login, remove `and not user.is_superuser`
            if user: # Changed from `user and not user.is_superuser` to allow superuser to log in as user
                # Manually set the default Django session key
                request.session['_auth_user_id'] = user.pk
                # Optionally, clear any existing admin session if they were logged in as admin in THIS TAB
                # (This prevents a superuser being logged in as admin AND user in the *same* browser tab)
                if '_auth_user_id_admin_portal' in request.session:
                    del request.session['_auth_user_id_admin_portal']

                messages.success(request, f"Welcome, {user.username}!")
                return redirect('user_dashboard')
            else:
                messages.error(request, 'Invalid credentials.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = UserLoginForm() # Initialize the form for GET requests

    return render(request, 'user/auth/login.html', {'form': form})


def register_view(request):
    # Check for admin login
    if request.session.get('_auth_user_id_admin_portal'):
        messages.warning(request, "Admin is currently logged in. Please logout to access user account.")
        return redirect('admin_dashboard')

    # Check for user login
    if request.session.get('_auth_user_id'):
        messages.info(request, "You are already logged in.")
        return redirect('user_dashboard')

    if request.method == 'POST':
        form = UserRegistrationForm(data=request.POST)
        if form.is_valid():
            user = form.save()
            # After successful registration, log them in as a regular user
            request.session['_auth_user_id'] = user.pk
            messages.success(request, "Account created successfully! Welcome to WEARIN.")
            return redirect('user_dashboard')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
            if form.non_field_errors():
                for error in form.non_field_errors():
                    messages.error(request, f"Error: {error}")
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
    # Clear only the default Django session key
    if '_auth_user_id' in request.session:
        del request.session['_auth_user_id']
        # Also clear the generic Django session. This is important.
        # If the user was also logged in as an 'admin' here by mistake, this clears it too for THIS TAB.
        request.session.flush() # Clear the entire session for this browser if logging out of user
                                # This ensures no leftover user auth.
    messages.info(request, "You have been logged out.")
    return redirect('home')


# --- Main Pages ---

def home_view(request):
    # Retrieve user based on the session (if any) to provide proper context
    user = None
    user_id = request.session.get('_auth_user_id')
    if user_id:
        try:
            User = get_user_model()
            user = User.objects.get(pk=user_id)
            request.user = user # Set request.user for this specific view context
        except User.DoesNotExist:
            pass # User ID in session but user not found, treat as anonymous

    new_products = Product.objects.filter(is_sold=False).order_by('-created_at')[:12]
    categories = Category.objects.all()

    wishlisted_product_ids = []
    if user and user.is_authenticated: # Use 'user' variable now
        wishlisted_product_ids = Wishlist.objects.filter(user=user).values_list('product_id', flat=True)

    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    return render(request, 'user/main/home.html', {
        'new_products': new_products,
        'categories': categories,
        'wishlisted_product_ids': list(wishlisted_product_ids),
        'cart_count': cart_count,
        'request_user': user # Pass 'user' object explicitly if needed in template for display
    })


@user_login_required # Apply the new decorator
def user_dashboard_view(request):
    return render(request, 'user/main/authenticated_home.html')


def shop_view(request):
    # Retrieve user based on the session (if any)
    user = None
    user_id = request.session.get('_auth_user_id')
    if user_id:
        try:
            User = get_user_model()
            user = User.objects.get(pk=user_id)
            request.user = user
        except User.DoesNotExist:
            pass

    products = Product.objects.filter(is_sold=False).order_by('-created_at')
    categories = Category.objects.all()

    wishlisted_product_ids = []
    if user and user.is_authenticated:
        wishlisted_product_ids = Wishlist.objects.filter(user=user).values_list('product_id', flat=True)

    return render(request, 'user/main/shop.html', {
        'products': products,
        'categories': categories,
        'wishlisted_product_ids': list(wishlisted_product_ids),
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

@user_login_required # Apply the new decorator
def cart_page_view(request):
    cart = request.session.get('cart', {})
    cart_items = []
    total_price = 0

    for product_id, item in list(cart.items()):
        try:
            product_obj = Product.objects.get(id=int(product_id))
            item_total = item['price'] * item['quantity']
            item['total'] = item_total
            item['product_obj'] = product_obj
            cart_items.append((product_id, item))
            total_price += item_total
        except Product.DoesNotExist:
            # If product no longer exists, remove it from cart
            if product_id in request.session['cart']:
                del request.session['cart'][product_id]
                request.session.modified = True
                messages.warning(request, f"A product was not found and has been removed from your cart.")

    context = {
        'cart_items': cart_items,
        'total_price': total_price,
    }
    return render(request, 'user/main/cart.html', context)


@user_login_required # Apply the new decorator
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
@user_login_required # Apply the new decorator
def remove_from_cart_view(request, product_id):
    cart = request.session.get('cart', {})
    product_id_str = str(product_id)

    if product_id_str in cart:
        product_name = cart[product_id_str]['name']
        del cart[product_id_str]
        request.session['cart'] = cart
        messages.info(request, f"{product_name} removed from your cart.")
    else:
        messages.warning(request, "Product not found in your cart.")

    return redirect('cart_page')


@require_POST
@user_login_required # Apply the new decorator
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

@user_login_required # Apply the new decorator
def wishlist_view(request):
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__category')
    context = {
        'wishlist_items': wishlist_items
    }
    return render(request, 'user/main/wishlist.html', context)


@user_login_required # Apply the new decorator
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


@user_login_required # Apply the new decorator
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