# core/templatetags/custom_filters.py
from django import template
from core.models import Cart, Wishlist # Import your Cart and Wishlist models

register = template.Library()

@register.simple_tag(takes_context=True)
def get_cart_count(context):
    request = context['request']
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            return cart.items.count() # Count items in the database cart
        except Cart.DoesNotExist:
            return 0
    else:
        # For anonymous users, count items in the session cart
        session_cart = request.session.get('cart', {})
        # Sum quantities if you store quantity in session, otherwise just count unique products
        return sum(item.get('quantity', 0) for item in session_cart.values())


@register.simple_tag(takes_context=True)
def get_wishlist_count(context):
    request = context['request']
    if request.user.is_authenticated:
        return Wishlist.objects.filter(user=request.user).count()
    return 0 # Anonymous users don't have a wishlist


# You can also create a full context processor if you prefer
# This approach is often cleaner for global variables
def cart_wishlist_counts(request):
    cart_count = 0
    wishlist_count = 0

    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            cart_count = sum(item.quantity for item in cart.items.all()) # Sum quantities for authenticated user
        except Cart.DoesNotExist:
            pass # cart_count remains 0

        wishlist_count = Wishlist.objects.filter(user=request.user).count()
    else:
        # For anonymous users, get cart count from session
        session_cart = request.session.get('cart', {})
        cart_count = sum(item.get('quantity', 0) for item in session_cart.values())

    return {
        'cart_count': cart_count,
        'wishlist_count': wishlist_count,
    }

register = template.Library()

@register.filter
def multiply(value, arg):
    try:
        return float(value) * float(arg)
    except:
        return ''

from django import template

register = template.Library()

@register.filter
def get_category_name(categories, id):
    try:
        return next(c.name for c in categories if str(c.id) == str(id))
    except StopIteration:
        return ""

@register.filter
def multiply(value, arg):
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return '' # Or raise an error, or return 0, depending on desired behavior for non-numeric input