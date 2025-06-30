from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.views.decorators.http import require_POST, require_GET
from django.http import JsonResponse
from django.urls import reverse
from django.conf import settings
from django.core.mail import send_mail
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.auth import login
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from core.decorators import user_login_required
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth import get_user

import json
from decimal import Decimal
from django.http import JsonResponse
from django.core.mail import send_mail
import razorpay
from django.views.decorators.csrf import csrf_exempt
# Import the correct models and forms
# IMPORTANT: Added NewsletterForm
from core.forms import UserLoginForm, UserRegistrationForm, ProfileForm, AddressForm, NewsletterForm
# IMPORTANT: Added NewsletterSubscriber
from core.models import Product, Category, Wishlist, Order, UserProfile, EmailOTP, Address, NewsletterSubscriber
from core.utils import send_otp_email

from django.contrib.auth.hashers import make_password
from django.db import transaction, IntegrityError # IMPORTANT: Added IntegrityError
import random
from django.utils import timezone

razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

def checkout_view(request):
    cart = request.session.get('cart', {})
    if not cart:
        return redirect('cart_page')

    total_price = sum(item['price'] * item['quantity'] for item in cart.values())

    payment = razorpay_client.order.create({
        "amount": int(total_price * 100),  # in paise
        "currency": "INR",
        "payment_capture": "1"
    })

    context = {
        "cart": cart,
        "total_price": total_price,
        "order_id": payment['id'],
        "razorpay_key": settings.RAZORPAY_KEY_ID,
    }
    return render(request, 'user/main/checkout.html', context)

@csrf_exempt
def payment_success_view(request):
    # Optional: Verify signature and update Order model
    return render(request, 'user/main/payment_success.html')

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
        return redirect('request_register_otp')

    if request.method == 'POST':
        entered_otp = request.POST.get('otp')
        try:
            otp_obj = EmailOTP.objects.get(email=email, otp=entered_otp, purpose="register")
            return redirect('register')
        except EmailOTP.DoesNotExist:
            messages.error(request, "Invalid OTP. Please try again.")
    return render(request, 'user/auth/verify_register_otp.html', {"email": email})

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
    orders = Order.objects.filter(user=request.user).order_by('-created_at')

    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    return render(request, 'user/main/profile.html', {
        'profile_form': profile_form,
        'user_addresses': user_addresses,
        'add_address_form': add_address_form,
        'cart_count': cart_count,
        'orders': orders
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
            return JsonResponse({'success': True, 'message': 'Address added successfully!'})
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


@user_login_required
def my_orders(request):
    orders = request.user.order_set.all()
    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())
    return render(request, 'user/profile/my_orders.html', {'orders': orders, 'cart_count': cart_count})


# --- Auth Views (No changes to these sections for address functionality directly) ---
User = get_user_model()

def login_view(request):
    list(messages.get_messages(request))

    user_session_key = request.COOKIES.get('user_sessionid')
    if user_session_key:
        session = SessionStore(session_key=user_session_key)
        user_id = session.get('_auth_user_id')

        if user_id:
            try:
                User.objects.get(pk=user_id)
                messages.info(request, "You are already logged in as a user.")
                return redirect('user_dashboard')
            except User.DoesNotExist:
                session.flush()

    if request.method == 'POST':
        form = UserLoginForm(request=request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            if user:
                login(request, user)
                request.session.save()

                response = redirect('user_dashboard')
                response.set_cookie(
                    'user_sessionid',
                    request.session.session_key
                )

                messages.success(request, f"Welcome, {user.username}!")
                return response
            else:
                messages.error(request, 'Invalid credentials.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = UserLoginForm()

    return render(request, 'user/auth/login.html', {'form': form})


def register_view(request):
    list(messages.get_messages(request))

    user_id = request.session.get('_auth_user_id')
    if user_id:
        User = get_user_model()
        try:
            User.objects.get(pk=user_id)
            messages.info(request, "You are already logged in.")
            return redirect('user_dashboard')
        except User.DoesNotExist:
            if '_auth_user_id' in request.session:
                del request.session['_auth_user_id']
            request.session.flush()
            messages.error(request, "Your previous user session was invalid. Please register or log in.")

    if request.method == 'POST':
        form = UserRegistrationForm(data=request.POST)
        if form.is_valid():
            user = form.save()
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

    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    return render(request, 'user/main/home.html', {
        'new_products': new_products,
        'categories': categories,
        'wishlisted_product_ids': list(wishlisted_product_ids),
        'cart_count': cart_count,
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

    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    sizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL']

    return render(request, 'user/main/shop.html', {
        'products': products,
        'categories': categories,
        'sizes': sizes,
        'cart_count': cart_count,
        'wishlisted_product_ids': list(wishlisted_product_ids),
    })


def product_detail_view(request, id=None):
    product = None
    if id:
        product = get_object_or_404(Product, id=id)

    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    return render(request, 'user/main/product_detail.html', {
        'product': product,
        'cart_count': cart_count,
        })


def policy_view(request):
    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())
    return render(request, 'user/main/static_pages/policy.html',{
        'cart_count': cart_count,
    })


def contact_view(request):
    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())
    return render(request, 'user/main/static_pages/contact.html',{
        'cart_count': cart_count,
    })


def about_view(request):
    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())
    return render(request, 'user/main/static_pages/about.html',{
        'cart_count': cart_count,
    })


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
    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())
    context = {
        'cart_items': cart_items,
        'total_price': total_price,
        'cart_count': cart_count,
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
def buy_now_checkout_view(request, product_id):
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id, is_sold=False)

        request.session['cart'] = {
            str(product_id): {
                'name': product.name,
                'price': float(product.price),  # 👈 Convert Decimal to float
                'quantity': 1,
                'image_url': product.images.first().image.url if product.images.exists() else '',
            }
        }
        request.session.modified = True
        return redirect('checkout')

    return redirect('product_detail', product_id=product_id)


@user_login_required
def wishlist_view(request):
    cart = request.session.get('cart', {})
    cart_count = sum(item.get('quantity', 0) for item in cart.values())

    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product', 'product__category')

    context = {
        'wishlist_items': wishlist_items,
        'cart_count': cart_count,
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
    

@csrf_protect # Protects against CSRF attacks
@require_POST # Ensures only POST requests are allowed
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
            fail_silently=False, # Set to True in production if you don't want exceptions
        )

        return JsonResponse({'success': True, 'message': 'Your message has been sent successfully!'})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON format.'}, status=400)
    except Exception as e:
        # Log the error for debugging
        print(f"Error sending contact email: {e}")
        return JsonResponse({'success': False, 'message': 'An unexpected error occurred. Please try again later.'}, status=500)
