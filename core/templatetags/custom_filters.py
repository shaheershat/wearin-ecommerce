from django import template
from core.models import Cart, Wishlist 
from decimal import Decimal 
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

@register.filter
def multiply(value, arg):
    try:
        return Decimal(value) * Decimal(arg)
    except (ValueError, TypeError):
        return Decimal('0.00') 
    
@register.filter
def get_category_name(categories, id):
    try:
        return next(c.name for c in categories if str(c.id) == str(id))
    except StopIteration:
        return ""
    
@register.filter
def multiply(value, arg):
    try:
        return Decimal(value) * Decimal(arg)
    except (ValueError, TypeError):
        return '' 

@register.filter
def subtract(value, arg):
    try:
        return Decimal(value) - Decimal(arg)
    except (ValueError, TypeError):
        return ''