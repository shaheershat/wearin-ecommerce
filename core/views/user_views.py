# core/views/user_views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate # ONLY authenticate for user login, NO django.contrib.auth.login/logout
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.urls import reverse
from django.conf import settings

# Import our custom decorators
from core.decorators import user_login_required

from core.forms import UserLoginForm, UserRegistrationForm, ProfileForm, AddressForm
from core.models import Product, Category, Wishlist, Order, UserProfile


@user_login_required
def update_profile(request):
    if request.method == 'POST':
        profile_form = ProfileForm(request.POST, instance=request.user)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, "Profile updated successfully!")
            return redirect('my_profile')
        else:
            for field, errors in profile_form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
            user_profile, created = UserProfile.objects.get_or_create(user=request.user)
            address_form = AddressForm(instance=user_profile)
            orders = Order.objects.filter(user=request.user).order_by('-created_at')
            return render(request, 'user/profile.html', {
                'profile_form': profile_form,
                'address_form': address_form,
                'user_address': user_profile,
                'orders': orders
            })
    return redirect('my_profile')


@user_login_required
def update_address(request):
    if request.method == 'POST':
        user_profile, created = UserProfile.objects.get_or_create(user=request.user)
        address_form = AddressForm(request.POST, instance=user_profile)
        if address_form.is_valid():
            address_form.save()
            messages.success(request, "Address updated successfully!")
            return redirect('my_profile')
        else:
            for field, errors in address_form.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
            profile_form = ProfileForm(instance=request.user)
            orders = Order.objects.filter(user=request.user).order_by('-created_at')
            return render(request, 'user/profile.html', {
                'profile_form': profile_form,
                'address_form': address_form,
                'user_address': user_profile,
                'orders': orders
            })
    return redirect('my_profile')


@user_login_required
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
            else:
                for field, errors in profile_form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
        elif 'update_address' in request.POST:
            address_form = AddressForm(request.POST, instance=user_profile)
            if address_form.is_valid():
                address_form.save()
                messages.success(request, "Address updated successfully!")
            else:
                for field, errors in address_form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
        else:
            messages.error(request, "Invalid form submission.")

    return render(request, 'user/profile.html', {
        'profile_form': profile_form,
        'address_form': address_form,
        'user_address': user_profile,
        'orders': orders
    })


@user_login_required
def my_profile(request):
    user_profile, created = UserProfile.objects.get_or_create(user=request.user)
    profile_form = ProfileForm(instance=request.user)
    address_form = AddressForm(instance=user_profile)
    return render(request, 'user/main/profile.html', {
        'profile_form': profile_form,
        'address_form': address_form,
        'user_address': user_profile
    })


@user_login_required
def my_address(request):
    user_profile, created = UserProfile.objects.get_or_create(user=request.user)
    address_form = AddressForm(instance=user_profile)
    return render(request, 'user/profile/my_address.html', {'address_form': address_form, 'user_address': user_profile})


@user_login_required
def my_orders(request):
    orders = request.user.order_set.all()
    return render(request, 'user/profile/my_orders.html', {'orders': orders})


# --- Auth Views ---

def login_view(request):
    list(messages.get_messages(request))

    # Crucial: Only check if *this* session (user session) is authenticated.
    user_id = request.session.get('_auth_user_id')
    if user_id:
        # Attempt to retrieve user just to confirm it's still valid
        User = get_user_model()
        try:
            User.objects.get(pk=user_id)
            messages.info(request, "You are already logged in as a user.")
            return redirect('user_dashboard')
        except User.DoesNotExist:
            # If user ID is in session but user doesn't exist, clear it.
            if '_auth_user_id' in request.session:
                del request.session['_auth_user_id']
            request.session.flush()
            messages.error(request, "Your previous user session was invalid. Please log in.")

    if request.method == 'POST':
        form = UserLoginForm(request=request, data=request.POST)
        if form.is_valid():
            user = form.get_user() # This implicitly uses authenticate()

            if user:
                # IMPORTANT: For user login, we ONLY set our custom session key.
                request.session['_auth_user_id'] = user.pk
                messages.success(request, f"Welcome, {user.username}!")
                return redirect('user_dashboard')
            else:
                messages.error(request, 'Invalid credentials.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = UserLoginForm()

    return render(request, 'user/auth/login.html', {'form': form})


def register_view(request):
    list(messages.get_messages(request))

    # Crucial: Only check if *this* session (user session) is authenticated.
    user_id = request.session.get('_auth_user_id')
    if user_id:
        # Attempt to retrieve user just to confirm it's still valid
        User = get_user_model()
        try:
            User.objects.get(pk=user_id)
            messages.info(request, "You are already logged in.")
            return redirect('user_dashboard')
        except User.DoesNotExist:
            # If user ID is in session but user doesn't exist, clear it.
            if '_auth_user_id' in request.session:
                del request.session['_auth_user_id']
            request.session.flush()
            messages.error(request, "Your previous user session was invalid. Please register or log in.")

    if request.method == 'POST':
        form = UserRegistrationForm(data=request.POST)
        if form.is_valid():
            user = form.save()

            # IMPORTANT: For user registration/login, only set your custom session key.
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
    # CRITICAL: This logout is ONLY for the user side.
    if '_auth_user_id' in request.session:
        del request.session['_auth_user_id']

    # Then, flush the entire session data for the current user session.
    request.session.flush()

    messages.info(request, "You have been logged out.")
    return redirect('home')


# --- Main Pages ---

def home_view(request):
    # For general views like home, we determine the user based on the active session.
    # AuthenticationMiddleware should populate request.user based on `user_sessionid` if present.
    user = request.user if request.user.is_authenticated else None

    # If request.user is not authenticated by Django (e.g., if you're not using
    # django.contrib.auth.login for regular users), but we have a custom session ID,
    # then manually set request.user for consistency.
    if not user:
        user_id = request.session.get('_auth_user_id')
        if user_id:
            try:
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                # Manually set request.user for consistency if needed by later middleware/context processors
                request.user = user
            except User.DoesNotExist:
                # If user ID in session is invalid, clear it
                if '_auth_user_id' in request.session:
                    del request.session['_auth_user_id']
                request.session.flush()
                user = None # Ensure user is None if session was invalid
                messages.error(request, "Your user session was invalid and has been cleared.")

    new_products = Product.objects.filter(is_sold=False).order_by('-created_at')[:12]
    categories = Category.objects.all()

    wishlisted_product_ids = []
    if user: # Use the determined `user` object
        wishlisted_product_ids = Wishlist.objects.filter(user=user).values_list('product_id', flat=True)

    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    return render(request, 'user/main/home.html', {
        'new_products': new_products,
        'categories': categories,
        'wishlisted_product_ids': list(wishlisted_product_ids),
        'cart_count': cart_count,
        'request_user': user # Pass the user object for template
    })


@user_login_required
def user_dashboard_view(request):
    # The @user_login_required decorator ensures request.user is set to the authenticated user.
    return render(request, 'user/main/authenticated_home.html')


def shop_view(request):
    user = request.user if request.user.is_authenticated else None
    if not user: # Same logic as home_view
        user_id = request.session.get('_auth_user_id')
        if user_id:
            try:
                User = get_user_model()
                user = User.objects.get(pk=user_id)
                request.user = user # Ensure request.user is set
            except User.DoesNotExist:
                if '_auth_user_id' in request.session:
                    del request.session['_auth_user_id']
                request.session.flush()
                user = None
                messages.error(request, "Your user session was invalid and has been cleared.")

    products = Product.objects.filter(is_sold=False).order_by('-created_at')
    categories = Category.objects.all()

    wishlisted_product_ids = []
    if user:
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


def policy_view(request):
    return render(request, 'user/main/static_pages/policy.html')


def contact_view(request):
    return render(request, 'user/main/static_pages/contact.html')


def about_view(request):
    return render(request, 'user/main/static_pages/about.html')


@user_login_required
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
            if product_id in request.session['cart']:
                del request.session['cart'][product_id]
                request.session.modified = True
                messages.warning(request, f"A product was not found and has been removed from your cart.")

    context = {
        'cart_items': cart_items,
        'total_price': total_price,
    }
    return render(request, 'user/main/cart.html', context)


@user_login_required
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
@user_login_required
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
@user_login_required
def buy_now_view(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    request.session['buy_now'] = {
        'id': product.id,
        'name': product.name,
        'price': float(product.price),
        'image': product.images.first().image.url if product.images.exists() else None,
    }
    return redirect('checkout_page')


@user_login_required
def wishlist_view(request):
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__category')
    context = {
        'wishlist_items': wishlist_items
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