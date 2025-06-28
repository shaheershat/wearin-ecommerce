# my_app/context_processors.py

from .models import Cart, Wishlist # Adjust import based on your models' location

def cart_wishlist_counts(request):
    cart_count = 0
    # You can keep this line if you still want to pass it as 0, or remove it entirely
    wishlist_count = 0 
    
    if request.user.is_authenticated:
        try:
            # For a typical e-commerce, it's cart items
            # Assuming user has a reverse relation 'cart_items' to CartItem model
            cart_count = sum(item.quantity for item in request.user.cart_items.all())
        except AttributeError:
            # Handle cases where user might not have 'cart_items' or if the model structure is different
            print("Warning: request.user.cart_items.all() failed. Check CartItem related_name or model structure.")
            cart_count = 0
        except Exception as e:
            print(f"Error fetching cart count: {e}")
            cart_count = 0

        # Since you don't want wishlist count, we can simplify this part or remove it.
        # If you remove the model import and this block, make sure no other part of your code relies on it.
        # For safety, we'll just ensure it's set to 0.
        # You could remove the Wishlist import if you are sure it's not used elsewhere in this file.
        # try:
        #     wishlist_count = Wishlist.objects.filter(user=request.user).count()
        # except Exception as e:
        #     print(f"Error fetching wishlist count: {e}")
        #     wishlist_count = 0

    return {
        'cart_count': cart_count,
        'wishlist_count': wishlist_count, # Still return it, but it will always be 0
    }