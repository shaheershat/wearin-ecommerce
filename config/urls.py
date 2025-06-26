from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from core.views.user_views import verify_reset_otp_view
from core.views import user_views, admin_views
from core.views.user_views import (
    register_view, request_register_otp_view, verify_register_otp_view,
    request_reset_otp_view, verify_reset_otp_view, reset_password_view,
    login_view, logout_view, confirm_code_view,
    wishlist_view, toggle_wishlist, remove_from_wishlist,
    remove_from_cart_view, update_profile, update_address
)

urlpatterns = [
    # Django's default admin (can be removed if not needed)
    path('admin/', admin.site.urls),

    # --- Custom Admin Auth ---
    path('admin-login/', admin_views.admin_login_view, name='admin_login'),
    path('admin-logout/', admin_views.admin_logout_view, name='admin_logout'),

    # --- Admin Panel ---
    path('admin-dashboard/', admin_views.admin_dashboard_view, name='admin_dashboard'),
    path('dashboard/products/', admin_views.admin_products_view, name='admin_product_list'),
    path('dashboard/products/<int:pk>/edit/', admin_views.edit_product_view, name='edit_product'),
    path('dashboard/products/<int:pk>/delete/', admin_views.delete_product_view, name='delete_product'),
    path('dashboard/upload/', admin_views.batch_upload_view, name='admin_upload_page'),
    path('dashboard/users/', admin_views.admin_user_list, name='admin_user_list'),
    path('dashboard/users/toggle/<int:user_id>/', admin_views.toggle_user_status, name='toggle_user_status'),
    path('dashboard/orders/', admin_views.admin_order_list, name='admin_order_list'),
    path('dashboard/orders/<int:order_id>/', admin_views.admin_order_detail_view, name='admin_order_detail'),

    # --- Coupon Management ---
    path('coupons/', admin_views.coupon_list, name='admin_coupon_list'),
    path('coupons/create/', admin_views.coupon_create, name='admin_coupon_create'),
    path('coupons/<int:pk>/edit/', admin_views.edit_coupon_view, name='admin_coupon_edit'),
    path('coupons/<int:pk>/delete/', admin_views.delete_coupon_view, name='admin_coupon_delete'),

    # --- User Auth ---
    path('login/', login_view, name='login'),
    path('register/', register_view, name='register'),
    path('logout/', logout_view, name='logout'),
    path('confirm-code/', confirm_code_view, name='confirm_code'),

    # --- Registration OTP ---
    path('register/otp/', request_register_otp_view, name='request_register_otp'),
    path('register/verify/', verify_register_otp_view, name='verify_register_otp'),

    # --- Forgot Password OTP ---
    path('forgot-password/', request_reset_otp_view, name='forgot_password'),
    path('forgot-password/verify/', verify_reset_otp_view, name='verify_reset_otp'),
    path('forgot-password/reset/', reset_password_view, name='reset_password'),

    # --- User Pages ---
    path('', user_views.home_view, name='home'),
    path('my-home/', user_views.user_dashboard_view, name='user_dashboard'),
    path('shop/', user_views.shop_view, name='shop'),
    path('product-detail/<int:id>/', user_views.product_detail_view, name='product_detail'),
    path('policy/', user_views.policy_view, name='policy'),
    path('contact/', user_views.contact_view, name='contact'),
    path('about/', user_views.about_view, name='about'),

    # --- Cart ---
    path('add-to-cart/<int:product_id>/', user_views.add_to_cart_view, name='add_to_cart'),
    path('buy-now/<int:product_id>/', user_views.buy_now_view, name='buy_now'),
    path('cart/', user_views.cart_page_view, name='cart_page'),
    path('checkout/', user_views.cart_page_view, name='checkout_page'),
    path('cart/remove/<int:product_id>/', remove_from_cart_view, name='remove_from_cart'),

    # --- Wishlist ---
    path('wishlist/', wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:product_id>/', toggle_wishlist, name='toggle_wishlist'),
    path('wishlist/remove/<int:product_id>/', remove_from_wishlist, name='remove_from_wishlist'),

    # --- User Profile ---
    path('profile/', user_views.my_profile, name='my_profile'),
    path('profile/address/', user_views.my_address, name='my_address'),
    path('profile/orders/', user_views.my_orders, name='my_orders'),
    path('profile/update/', update_profile, name='update_profile'),
    path('profile/update-address/', update_address, name='update_address'),

    # --- Social Login ---
    path('accounts/', include('allauth.urls')),
]

# Media files for development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)




