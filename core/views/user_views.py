from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.views.decorators.http import require_POST, require_GET
from django.http import JsonResponse
from django.urls import reverse
from django.db.models import Q
from django.conf import settings
from django.contrib.auth.backends import ModelBackend 
from django.db import models
from django.core.mail import send_mail
from django.contrib.auth import login
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from core.decorators import user_login_required
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth import get_user
import json
from decimal import Decimal
from core.models import Wallet, WalletTransaction
from core.models import OrderItem
from core.models import Cart, CartItem
from django.http import JsonResponse
from django.core.mail import send_mail
import razorpay
from django.views.decorators.csrf import csrf_exempt
from core.forms import UserLoginForm, UserRegistrationForm, ProfileForm, AddressForm, NewsletterForm
from core.models import Product, Category, Wishlist, Order, UserProfile, EmailOTP, Address, NewsletterSubscriber
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
        messages.error(request, 'This order cannot be cancelled.')
        return redirect('my_profile')

    order.status = 'Cancelled'
    order.save()

    # Refund amount to wallet
    wallet = request.user.wallet
    wallet.credit(order.total_price)

    # Add wallet transaction record
    WalletTransaction.objects.create(
        wallet=wallet,
        transaction_type='refund',
        amount=order.total_price,
        reason=f"Refund for cancelled order #{order.id}"
    )

    messages.success(request, f"Order has been cancelled and ₹{order.total_price} has been refunded to your wallet.")
    return redirect('my_profile')

@user_login_required
def return_order_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    
    if order.status != 'Delivered':
        messages.error(request, 'Only delivered orders can be returned.')
        return redirect('my_profile')

    if order.return_status == 'Requested':
        messages.info(request, 'Return already requested.')
        return redirect('my_profile')

    order.return_status = 'Requested'
    order.save()
    messages.success(request, 'Return request submitted.')
    return redirect('my_profile')

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
def checkout_view(request):
    cart = request.session.get('cart', {})
    buy_now = request.session.get('buy_now_item')
    cart_items = []
    subtotal = 0

    # Buy Now Logic
    if buy_now:
        try:
            product = Product.objects.get(id=buy_now['id'])
            item = {
                'product': product,
                'quantity': buy_now['quantity'],
                'total_price': product.price * buy_now['quantity']
            }
            cart_items.append(item)
            subtotal = item['total_price']
        except Product.DoesNotExist:
            messages.error(request, "Product no longer available.")
            return redirect('cart_page')

    # Session Cart Logic
    elif cart:
        for pid, item in cart.items():
            try:
                product = Product.objects.get(id=int(pid))
                total = product.price * item['quantity']
                cart_items.append({
                    'product': product,
                    'quantity': item['quantity'],
                    'total_price': total
                })
                subtotal += total
            except Product.DoesNotExist:
                continue

    # DB Cart Fallback
    elif request.user.is_authenticated:
        try:
            db_cart = Cart.objects.get(user=request.user)
            session_cart = {}
            for item in db_cart.items.select_related('product'):
                product = item.product
                total = float(product.price) * item.quantity
                cart_items.append({
                    'product': product,
                    'quantity': item.quantity,
                    'total_price': total
                })
                subtotal += total
                session_cart[str(product.id)] = {
                    'name': product.name,
                    'price': float(product.price),
                    'quantity': item.quantity,
                    'image_url': product.images.first().image.url if product.images.exists() else '',
                }
            request.session['cart'] = session_cart
            request.session.modified = True
        except Cart.DoesNotExist:
            pass

    if subtotal <= 0 or not cart_items:
        messages.error(request, "Your cart is empty or contains invalid items.")
        return redirect('cart_page')

    total_price = subtotal

    # Razorpay Order
    payment = razorpay_client.order.create({
        "amount": int(total_price * 100),
        "currency": "INR",
        "payment_capture": "1"
    })

    addresses = Address.objects.filter(user=request.user)
    selected = addresses.filter(is_default=True).first()

    # Wallet balance
    wallet_balance = 0
    if request.user.is_authenticated:
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        wallet_balance = wallet.balance

    return render(request, 'user/main/checkout.html', {
        "cart_items": cart_items,
        "subtotal": subtotal,
        "total_price": total_price,
        "order_id": payment['id'],
        "razorpay_key": settings.RAZORPAY_KEY_ID,
        "addresses": addresses,
        "selected_address_id": selected.id if selected else None,
        "wallet_balance": wallet_balance
    })



@csrf_exempt
@user_login_required
def payment_success_view(request):
    user = request.user
    cart = request.session.get('cart', {})
    buy_now = request.session.get('buy_now_item')

    payment_method = request.POST.get('payment_method', 'razorpay')
    address_id = request.POST.get('address_id') or request.session.get('selected_address_id')

    if not address_id:
        messages.error(request, "No address selected.")
        return redirect('checkout')

    try:
        address = Address.objects.get(id=address_id, user=user)
    except Address.DoesNotExist:
        messages.error(request, "Invalid address.")
        return redirect('checkout')

    if not cart and not buy_now:
        messages.error(request, "Your cart is empty.")
        return redirect('cart_page')

    try:
        with transaction.atomic():
            total_price = Decimal('0.00')
            order = Order.objects.create(
                user=user,
                address=address,
                status='Pending',
                total_price=Decimal('0.00')
            )

            # Buy Now Flow
            if buy_now:
                product = Product.objects.get(id=buy_now['id'])
                quantity = buy_now.get('quantity', 1)
                price = Decimal(product.price)

                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price_at_purchase=price
                )
                total_price = price * quantity
                product.is_sold = True
                product.save()
                Wishlist.objects.filter(user=user, product=product).delete()
                del request.session['buy_now_item']

            # Cart Flow
            elif cart:
                for pid, item in cart.items():
                    product = Product.objects.get(id=int(pid))
                    quantity = item['quantity']
                    price = Decimal(product.price)

                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=quantity,
                        price_at_purchase=price
                    )
                    total_price += price * quantity
                    product.is_sold = True
                    product.save()
                    Wishlist.objects.filter(user=user, product=product).delete()

                request.session['cart'] = {}
                Cart.objects.filter(user=user).delete()

            # Payment Method Handling
            if payment_method == 'wallet':
                wallet = Wallet.objects.get(user=user)
                if wallet.balance < total_price:
                    messages.error(request, "Insufficient wallet balance.")
                    return redirect('checkout')
                
                # Deduct wallet balance
                wallet.balance -= total_price
                wallet.save()

                # Record debit transaction
                WalletTransaction.objects.create(
                    wallet=wallet,
                    amount=-total_price,
                    reason=f"Payment for Order #{order.id}"
                )

                order.payment_method = 'Wallet'

            elif payment_method == 'cod':
                if total_price > 1000:
                    messages.error(request, "COD not available for orders above ₹1000.")
                    return redirect('checkout')
                order.payment_method = 'COD'

            else:
                order.payment_method = 'Razorpay'

            order.total_price = total_price
            order.save()

            messages.success(request, "Order placed successfully!")
            return render(request, 'user/main/payment_success.html', {'order': order})

    except Exception as e:
        print("❌ Order Error:", e)
        messages.error(request, "An error occurred during order processing.")
        return redirect('checkout')


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
    orders = Order.objects.filter(user=request.user).order_by('-created_at')

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

@user_login_required
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

    # --- MODIFIED LINE FOR RANDOM ORDERING ---
    # products = Product.objects.filter(is_sold=False).order_by('-created_at') # Original line
    products = Product.objects.filter(is_sold=False).order_by('?') # Changed to randomize
    # --- END MODIFIED LINE ---

    categories = Category.objects.all()
    sizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL']

    # Filters
    selected_categories = request.POST.getlist('category') or request.GET.getlist('category')
    selected_sizes = request.POST.getlist('size[]') or request.GET.getlist('size[]')
    max_price = request.POST.get('max_price') or request.GET.get('max_price') or '10000'

    # --- DEBUGGING STEP: Print the raw GET parameters and the search_query ---
    print(f"Request GET parameters: {request.GET}")
    search_query = request.GET.get('query')
    print(f"Search Query obtained: '{search_query}'")
    # --- END DEBUGGING STEP ---


    # Apply filters
    q = Q() # Initialize an empty Q object
    
    if selected_categories:
        q &= Q(category__id__in=selected_categories)
        print(f"Applied category filter: {selected_categories}")

    try:
        if max_price: # Only apply if max_price is not empty
            q &= Q(price__lte=Decimal(max_price)) # Use Decimal for price comparison if your model uses DecimalField
            print(f"Applied max_price filter: {max_price}")
    except ValueError:
        print("Invalid max_price value provided.")
        pass # Handle case where max_price is not a valid number

    if selected_sizes:
        q &= Q(size__in=selected_sizes)
        print(f"Applied size filter: {selected_sizes}")

    # Apply Search Query Filter
    if search_query:
        print(f"Applying search filter for query: '{search_query}'")
        q &= (Q(name__icontains=search_query) |
              Q(description__icontains=search_query))
    else:
        print("No search query provided.")


    products = products.filter(q)
    print(f"Total products after all filters: {products.count()}")
    # --- END DEBUGGING STEP ---


    # Wishlist
    wishlisted_product_ids = []
    if user:
        wishlisted_product_ids = Wishlist.objects.filter(user=user).values_list('product_id', flat=True)

    context = {
        'products': products,
        'categories': categories,
        'sizes': sizes,
        'wishlisted_product_ids': list(wishlisted_product_ids),
        'selected_categories': selected_categories,
        'selected_sizes': selected_sizes,
        'selected_max_price': max_price,
        'search_query': search_query, # Pass search_query to template to pre-fill the search box
    }

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'user/main/shop_ajax_results.html', context)

    return render(request, 'user/main/shop.html', context)


def product_detail_view(request, id=None):
    product = None
    if id:
        product = get_object_or_404(Product, id=id)
    return render(request, 'user/main/product_detail.html', {
        'product': product,
        
        })


def policy_view(request):

    return render(request, 'user/main/static_pages/policy.html',{
        
    })


def contact_view(request):

    return render(request, 'user/main/static_pages/contact.html',{
        
    })


def about_view(request):

    return render(request, 'user/main/static_pages/about.html',{
        
    })

@user_login_required
def cart_page_view(request):
    cart_items = []
    total_price = 0

    #  IF USER IS LOGGED IN → LOAD FROM DB CART
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            for item in cart.items.select_related('product'):
                product = item.product
                item_data = {
                    'name': product.name,
                    'price': float(product.price),
                    'quantity': item.quantity,
                    'total': float(product.price) * item.quantity,
                    'image_url': product.images.first().image.url if product.images.exists() else '',
                }
                cart_items.append((product.id, item_data))
                total_price += item_data['total']
        except Cart.DoesNotExist:
            cart_items = []
            total_price = 0

    else:
        #  FALLBACK TO SESSION CART
        cart = request.session.get('cart', {})
        for product_id, item in list(cart.items()):
            try:
                product_obj = Product.objects.get(id=int(product_id))
                item_total = item['price'] * item['quantity']
                item['total'] = item_total
                item['product_obj'] = product_obj
                cart_items.append((product_id, item))
                total_price += item_total
            except Product.DoesNotExist:
                del request.session['cart'][product_id]
                request.session.modified = True
                messages.warning(request, "An item in your cart was removed because it no longer exists.")

    cart_count = sum(item['quantity'] for _, item in cart_items)

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

    #  Update session cart
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
    request.session.modified = True

    #  Update DB cart
    if request.user.is_authenticated:
        from core.models import Cart, CartItem
        user_cart, _ = Cart.objects.get_or_create(user=request.user)
        cart_item, created = CartItem.objects.get_or_create(cart=user_cart, product=product)
        if not created:
            cart_item.quantity += 1
        cart_item.save()

    return redirect('cart_page')

@require_POST
@user_login_required
def remove_from_cart_view(request, product_id):
    product_id_str = str(product_id)
    cart = request.session.get('cart', {})

    #  Remove from session
    if product_id_str in cart:
        product_name = cart[product_id_str]['name']
        del cart[product_id_str]
        request.session['cart'] = cart
        request.session.modified = True
        messages.info(request, f"{product_name} removed from your cart.")
    else:
        messages.warning(request, "Product not found in your cart.")

    #  Remove from DB
    if request.user.is_authenticated:
        from core.models import Cart, CartItem
        try:
            user_cart = Cart.objects.get(user=request.user)
            CartItem.objects.filter(cart=user_cart, product_id=product_id).delete()
        except Cart.DoesNotExist:
            pass

    return redirect('cart_page')

@require_POST
@user_login_required
def buy_now_checkout_view(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_sold=False)

    # Save buy now item as a separate session entry
    request.session['buy_now_item'] = {
        'id': product.id,
        'name': product.name,
        'price': float(product.price),
        'quantity': 1,
        'image_url': product.images.first().image.url if product.images.exists() else '',
    }
    request.session.modified = True

    return redirect('checkout')

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