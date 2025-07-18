# core/templatetags/custom_filters.py
from django import template
from core.models import Cart, Wishlist # Import your Cart and Wishlist models
from decimal import Decimal # Import Decimal for more accurate currency handling

register = template.Library()

@register.simple_tag(takes_context=True)
def get_cart_count(context):
    request = context['request']
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            return cart.items.aggregate(total_quantity=models.Sum('quantity'))['total_quantity'] or 0
        except Cart.DoesNotExist:
            return 0
    else:
        session_cart = request.session.get('cart', {})
        return sum(item.get('quantity', 0) for item in session_cart.values())

@register.simple_tag(takes_context=True)
def get_wishlist_count(context):
    request = context['request']
    if request.user.is_authenticated:
        return Wishlist.objects.filter(user=request.user).count()
    return 0

# You can also create a full context processor if you prefer
# This approach is often cleaner for global variables
# (This function is likely used in core/context_processors.py, not directly here)
# def cart_wishlist_counts(request):
#     cart_count = 0
#     wishlist_count = 0
#     if request.user.is_authenticated:
#         try:
#             cart = Cart.objects.get(user=request.user)
#             cart_count = sum(item.quantity for item in cart.items.all())
#         except Cart.DoesNotExist:
#             pass
#         wishlist_count = Wishlist.objects.filter(user=request.user).count()
#     else:
#         session_cart = request.session.get('cart', {})
#         cart_count = sum(item.get('quantity', 0) for item in session_cart.values())
#     return {'cart_count': cart_count, 'wishlist_count': wishlist_count,}


@register.filter
def multiply(value, arg):
    try:
        # Convert to Decimal for precise financial calculations
        return Decimal(value) * Decimal(arg)
    except (ValueError, TypeError):
        return Decimal('0.00') # Return 0.00 for non-numeric input, not an empty string
    
@register.filter
def get_category_name(categories, id):
    try:
        return next(c.name for c in categories if str(c.id) == str(id))
    except StopIteration:
        return ""