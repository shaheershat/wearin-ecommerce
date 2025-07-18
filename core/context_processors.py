from core.models import Cart, CartItem, OfferBanner, Coupon # Ensure Coupon is imported
from django.db.models import Sum
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

def cart_context(request):
    """
    Context processor to add cart information to all templates.
    """
    cart_count = 0
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            # Only count items that are not sold and have stock
            cart_count = CartItem.objects.filter(
                cart=cart,
                product__is_sold=False,
                product__stock_quantity__gt=0
            ).aggregate(Sum('quantity'))['quantity__sum'] or 0
        except Cart.DoesNotExist:
            pass # No cart for the user yet
    return {'cart_count': cart_count}


def offer_context(request):
    """
    Context processor to add active offer banner and other general offers to all templates.
    """
    active_offer_banner = None
    try:
        # Fetch the single active banner for the main site
        active_offer_banner = OfferBanner.objects.filter(is_active=True).first()
    except Exception as e:
        logger.error(f"Error fetching active offer banner: {e}", exc_info=True)
        active_offer_banner = None # Ensure it's None if an error occurs

    # You can add logic here for other general offers if needed,
    # but the primary error was with `tag_text` on `Coupon` objects.
    # For now, we'll focus on the banner.

    return {
        'active_offer_banner': active_offer_banner,
        # 'active_general_offers': active_general_offers, # Example for other offers
    }

