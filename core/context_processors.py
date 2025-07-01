# core/context_processors.py

from core.models import Cart, CartItem  # Use correct path to your models

def cart_context(request):
    cart_count = 0

    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            cart_count = CartItem.objects.filter(cart=cart).count()
        except Cart.DoesNotExist:
            pass
    else:
        cart = request.session.get('cart', {})
        cart_count = sum(item.get('quantity', 1) for item in cart.values())

    print("🛒 cart_context running, cart_count =", cart_count)  # 👈 Add this line
    return {'cart_count': cart_count}
