import random
from decimal import Decimal
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.contrib import messages # Needed if you want messages inside utils
from django.conf import settings
from django.core.mail import send_mail # New import for OTP email

# Import your models used within this utility function
from .models import Cart, CartItem, Product, Offer, Coupon, EmailOTP, Order # Ensure EmailOTP, Order are imported if used in utils


# Define constants if they are not in settings.py
# If they are in settings.py, remove these and use settings.CONSTANT_NAME
DEFAULT_SHIPPING_CHARGE = Decimal('50.00')
MIN_PURCHASE_FOR_FREE_SHIPPING = Decimal('1000.00')
CART_RESERVATION_TIME_MINUTES = 15


# --- OTP Related Functions ---
def generate_otp():
    """Generates a 6-digit random OTP."""
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp):
    """
    Sends an OTP to the specified email address.
    Requires settings.DEFAULT_FROM_EMAIL to be configured.
    """
    subject = 'Your Wearin OTP Code'
    message = f'Hi,\n\nYour OTP for verification is: {otp}\n\nIf you didn’t request this, please ignore this email.\n\n– Wearin Team'
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email])
    except Exception as e:
        # Log the error if email sending fails
        # You might want to use Django's logging system here
        print(f"Error sending OTP email to {email}: {e}")
        # Optionally re-raise or handle as needed
        raise


# --- Cart/Checkout Related Utility Functions ---
def get_cart_items_data(request):
    """
    Calculates and returns a structured dictionary of cart items,
    applying product-level offers (BOGO, Percentage, Fixed Amount),
    and determining original/discounted subtotals and free shipping status.
    Handles both regular cart and 'Buy Now' items.
    """
    cart_items_for_order_display = []
    total_cart_items_original_price = Decimal('0.00')
    offer_level_discount = Decimal('0.00')
    is_free_shipping = False # Determined by product offers or site-wide offers

    products_grouped_by_bogo_offer = {}
    items_for_standard_pricing = []

    # First, try to get the regular cart items
    user_cart_obj = None
    try:
        user_cart_obj = Cart.objects.get(user=request.user)
        cart_item_ids_to_delete = []
        cart_items_queryset = user_cart_obj.items.select_for_update().select_related('product').prefetch_related('product__offers')

        for item in cart_items_queryset:
            product = item.product
            if product.is_sold:
                messages.error(request, f"'{product.name}' is already sold and removed from your cart.")
                cart_item_ids_to_delete.append(item.id)
                continue
            if product.reserved_by_user:
                if product.reserved_by_user != request.user:
                    messages.warning(request, f"'{product.name}' is now reserved by another customer and removed from your cart.")
                    cart_item_ids_to_delete.append(item.id)
                    continue
                elif not product.is_currently_reserved:
                    messages.warning(request, f"Your reservation for '{product.name}' has expired and it was removed from your cart.")
                    product.release_reservation()
                    product.save()
                    cart_item_ids_to_delete.append(item.id)
                    continue

            original_item_total = product.price * item.quantity
            total_cart_items_original_price += original_item_total

            active_product_offers = [
                offer for offer in product.offers.all() if offer.is_currently_active
            ]

            bogo_offer_found_for_product = False
            for offer in active_product_offers:
                if offer.offer_type in ['BOGO1', 'BOGO2']:
                    offer_key = offer.id
                    if offer_key not in products_grouped_by_bogo_offer:
                        products_grouped_by_bogo_offer[offer_key] = {
                            'offer': offer,
                            'items': []
                        }
                    products_grouped_by_bogo_offer[offer_key]['items'].append({
                        'product': product,
                        'quantity': item.quantity,
                        'original_item_total': original_item_total,
                        'cart_item_id': item.id
                    })
                    bogo_offer_found_for_product = True
                    break
            
            if not bogo_offer_found_for_product:
                applied_simple_offer = None
                for offer in active_product_offers:
                    if offer.offer_type == 'FREESHIP':
                        is_free_shipping = True
                    elif offer.offer_type in ['PERCENTAGE', 'AMOUNT']:
                        applied_simple_offer = offer
                        break
                
                items_for_standard_pricing.append({
                    'product': product,
                    'quantity': item.quantity,
                    'original_item_total': original_item_total,
                    'applied_offer': applied_simple_offer
                })

        if cart_item_ids_to_delete:
            CartItem.objects.filter(id__in=cart_item_ids_to_delete).delete()

    except Cart.DoesNotExist:
        pass # No regular cart found for the user

    # Handle 'Buy Now' item if no regular cart items or it's specifically a 'buy now' flow
    buy_now_active = request.session.get('buy_now_active', False)
    if buy_now_active and not (products_grouped_by_bogo_offer or items_for_standard_pricing):
        buy_now_product_id = request.session.get('buy_now_item_id')
        buy_now_quantity = request.session.get('buy_now_quantity')
        
        if buy_now_product_id and buy_now_quantity:
            try:
                product = Product.objects.select_for_update().get(id=buy_now_product_id)
                if product.is_sold:
                    messages.error(request, f"'{product.name}' from 'Buy Now' is already sold.")
                    del request.session['buy_now_active']
                    del request.session['buy_now_item_id']
                    del request.session['buy_now_quantity']
                    request.session.modified = True
                    return {
                        'items': [],
                        'subtotal_original': Decimal('0.00'),
                        'discount_from_offers': Decimal('0.00'),
                        'subtotal_after_offers': Decimal('0.00'),
                        'is_free_shipping': False,
                        'cart_obj': None,
                    }

                if product.reserved_by_user:
                    if product.reserved_by_user != request.user:
                        messages.warning(request, f"'{product.name}' from 'Buy Now' is reserved by another customer.")
                        del request.session['buy_now_active']
                        del request.session['buy_now_item_id']
                        del request.session['buy_now_quantity']
                        request.session.modified = True
                        return { 'items': [], 'subtotal_original': Decimal('0.00'), 'discount_from_offers': Decimal('0.00'), 'subtotal_after_offers': Decimal('0.00'), 'is_free_shipping': False, 'cart_obj': None,}
                    elif not product.is_currently_reserved:
                        messages.warning(request, f"Your 'Buy Now' reservation for '{product.name}' has expired.")
                        product.release_reservation()
                        product.save()
                        del request.session['buy_now_active']
                        del request.session['buy_now_item_id']
                        del request.session['buy_now_quantity']
                        request.session.modified = True
                        return { 'items': [], 'subtotal_original': Decimal('0.00'), 'discount_from_offers': Decimal('0.00'), 'subtotal_after_offers': Decimal('0.00'), 'is_free_shipping': False, 'cart_obj': None,}
                else:
                    product.reserved_by_user = request.user
                    product.reservation_expires_at = timezone.now() + timedelta(minutes=CART_RESERVATION_TIME_MINUTES)
                    product.save()
                    messages.info(request, f"'{product.name}' reserved for 'Buy Now'.")

                original_item_total = product.price * buy_now_quantity
                total_cart_items_original_price += original_item_total

                applied_simple_offer = None
                active_product_offers = [
                    offer for offer in product.offers.all() if offer.is_currently_active
                ]
                for offer in active_product_offers:
                    if offer.offer_type == 'FREESHIP':
                        is_free_shipping = True
                    elif offer.offer_type in ['PERCENTAGE', 'AMOUNT']:
                        applied_simple_offer = offer
                        break
                
                items_for_standard_pricing.append({
                    'product': product,
                    'quantity': buy_now_quantity,
                    'original_item_total': original_item_total,
                    'applied_offer': applied_simple_offer
                })

            except Product.DoesNotExist:
                messages.error(request, "The 'Buy Now' product is no longer available.")
                del request.session['buy_now_active']
                del request.session['buy_now_item_id']
                del request.session['buy_now_quantity']
                request.session.modified = True
                return { 'items': [], 'subtotal_original': Decimal('0.00'), 'discount_from_offers': Decimal('0.00'), 'subtotal_after_offers': Decimal('0.00'), 'is_free_shipping': False, 'cart_obj': None,}
    
    # --- Step 1: Calculate discounts from BOGO offers ---
    for offer_key, offer_data in products_grouped_by_bogo_offer.items():
        offer = offer_data['offer']
        items_under_bogo = offer_data['items']

        total_bogo_quantity = sum(item['quantity'] for item in items_under_bogo)
        buy_qty = offer.buy_quantity
        get_qty = offer.get_quantity

        if buy_qty is None or get_qty is None or buy_qty <= 0 or get_qty <= 0:
            for item_data in items_under_bogo:
                cart_items_for_order_display.append({
                    'product': item_data['product'],
                    'quantity': item_data['quantity'],
                    'total_price': item_data['original_item_total'],
                    'original_item_total': item_data['original_item_total'],
                    'discount_applied': Decimal('0.00'),
                    'offer_tag': offer.tag_text,
                })
            continue

        products_per_cycle = buy_qty + get_qty
        num_cycles = total_bogo_quantity // products_per_cycle
        num_free_products_overall = num_cycles * get_qty

        sorted_bogo_items = sorted(items_under_bogo, key=lambda x: x['product'].price, reverse=True)
        remaining_free_products_to_distribute = num_free_products_overall

        for item_data in sorted_bogo_items:
            product = item_data['product']
            quantity_in_cart = item_data['quantity']
            actual_billed_quantity = quantity_in_cart
            item_discount = Decimal('0.00')

            if remaining_free_products_to_distribute > 0:
                free_from_this_item = min(quantity_in_cart, remaining_free_products_to_distribute)
                actual_billed_quantity = quantity_in_cart - free_from_this_item
                item_discount = free_from_this_item * product.price
                remaining_free_products_to_distribute -= free_from_this_item
            
            offer_level_discount += item_discount

            cart_items_for_order_display.append({
                'product': product,
                'quantity': quantity_in_cart,
                'total_price': actual_billed_quantity * product.price,
                'original_item_total': item_data['original_item_total'],
                'discount_applied': item_discount,
                'offer_tag': offer.tag_text,
            })

    # --- Step 2: Calculate discounts from Percentage/Amount offers and add regular items ---
    for item_data in items_for_standard_pricing:
        product = item_data['product']
        quantity = item_data['quantity']
        original_item_total = item_data['original_item_total']
        applied_offer = item_data['applied_offer']

        item_total_after_offer = original_item_total
        item_discount = Decimal('0.00')
        offer_tag = None

        if applied_offer:
            offer_tag = applied_offer.tag_text
            if applied_offer.offer_type == 'PERCENTAGE':
                item_discount = original_item_total * (applied_offer.discount_percentage / Decimal(100))
                item_total_after_offer = original_item_total - item_discount
            elif applied_offer.offer_type == 'AMOUNT':
                item_discount = applied_offer.discount_amount * quantity
                item_total_after_offer = max(Decimal('0.00'), original_item_total - item_discount)
            
            offer_level_discount += item_discount

        cart_items_for_order_display.append({
            'product': product,
            'quantity': quantity,
            'total_price': item_total_after_offer,
            'original_item_total': original_item_total,
            'discount_applied': item_discount,
            'offer_tag': offer_tag,
        })
    
    # --- Final check: If no items remain after all checks, return empty data ---
    if not cart_items_for_order_display:
        return {
            'items': [],
            'subtotal_original': Decimal('0.00'),
            'discount_from_offers': Decimal('0.00'),
            'subtotal_after_offers': Decimal('0.00'),
            'is_free_shipping': False,
            'cart_obj': user_cart_obj,
        }

    # --- Step 3: Determine Subtotal (after product-level offers) ---
    subtotal_after_offers = sum(item['total_price'] for item in cart_items_for_order_display)

    return {
        'items': cart_items_for_order_display,
        'subtotal_original': total_cart_items_original_price,
        'discount_from_offers': offer_level_discount,
        'subtotal_after_offers': subtotal_after_offers,
        'is_free_shipping': is_free_shipping,
        'cart_obj': user_cart_obj,
    }


def validate_coupon(coupon, user, subtotal_after_offers, cart_items_list):
    """
    Validates a coupon against its conditions and current cart/user state.
    cart_items_list should be the processed list from get_cart_items_data,
    containing dictionaries with 'product' and 'quantity'.
    Returns (True, None) if valid, or (False, error_message) if invalid.
    """
    now = timezone.now()

    if not coupon.is_active:
        return False, "Coupon is not active."
    if coupon.valid_from and now < coupon.valid_from:
        return False, "Coupon is not yet valid."
    if coupon.valid_to and now > coupon.valid_to:
        return False, "Coupon has expired."
    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        return False, "Coupon usage limit reached."

    # User-specific conditions
    # Assuming 'Order' is the model used to track user orders
    user_orders_count = Order.objects.filter(user=user, payment_status='Paid').count()

    if coupon.applies_to_new_users_only:
        if user_orders_count > 0:
            return False, "This coupon is for new users only."
    elif coupon.min_orders_for_user is not None:
        if user_orders_count < coupon.min_orders_for_user:
            return False, f"You need at least {coupon.min_orders_for_user} previous orders to use this coupon."

    # Cart conditions
    if subtotal_after_offers < coupon.min_purchase:
        return False, f"Minimum purchase of ₹{coupon.min_purchase:.2f} required."

    # Assuming cart_items_list contains dictionaries with 'product' (Product model) and 'quantity'
    if coupon.min_unique_products_in_cart is not None:
        unique_products = set()
        for item in cart_items_list:
            if isinstance(item, dict) and 'product' in item:
                unique_products.add(item['product'].id)
            elif hasattr(item, 'product'):
                 unique_products.add(item.product.id)
        if len(unique_products) < coupon.min_unique_products_in_cart:
            return False, f"At least {coupon.min_unique_products_in_cart} unique products required in cart."

    if coupon.min_total_items_in_cart is not None:
        total_items_quantity = sum(item['quantity'] for item in cart_items_list)
        if total_items_quantity < coupon.min_total_items_in_cart:
            return False, f"At least {coupon.min_total_items_in_cart} total items in cart required."

    return True, None