# config/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# Import views from core.views.user_views
from core.views import user_views, admin_views
from core.views.user_views import (
    register_view, request_register_otp_view, verify_register_otp_view,
    request_reset_otp_view, verify_reset_otp_view, reset_password_view,
    login_view, logout_view, confirm_code_view,
    wishlist_view, toggle_wishlist, remove_from_wishlist,
    remove_from_cart_view, update_profile,
    add_address,
    edit_address, set_default_address, delete_address, get_address_data,
    subscribe_newsletter,
    add_address_form_view,
    notify_me_view,
    # NEW: Import the new return views
    get_order_items_for_return, create_return_request,
)

urlpatterns = [
    # Django's default admin
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
    path('orders/<int:order_id>/update-status/', admin_views.admin_update_order_status, name='admin_update_order_status'),
    path('get-categories/', admin_views.get_categories, name='get_categories'),
    path('create-manual-product/', admin_views.create_manual_product, name='create_manual_product'),
    path('create-category/', admin_views.create_category, name='create_category'),
    path('dashboard/sales/', admin_views.admin_sales_view, name='admin_sales'),
    path('dashboard/sales/export-excel/', admin_views.export_sales_excel_view, name='export_sales_excel'),
    path('dashboard/sales/export-pdf/', admin_views.export_sales_pdf, name='export_sales_pdf'),
    
    # REMOVED: Direct approve/reject return URLs from admin
    # path('orders/<int:order_id>/approve-return/', admin_views.approve_return_view, name='admin_approve_return'),
    # path('orders/<int:order_id>/reject-return/', admin_views.reject_return_view, name='admin_reject_return'),
    
    # UPDATED/NEW: Admin Return Request API Endpoints
    # The old admin_return_request_detail is effectively replaced by an AJAX endpoint
    # path('admin/return-request/<int:order_id>/', admin_views.admin_return_request_detail, name='admin_return_request_detail'), # Old
    path('api/admin/returns/<int:request_id>/details/', admin_views.get_return_request_for_admin_modal, name='admin_get_return_request_details'), # NEW
    path('api/admin/returns/<int:request_id>/process/', admin_views.process_admin_return_request, name='admin_process_return_request'), # NEW


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
    path('buy-now/<int:product_id>/checkout/', user_views.buy_now_checkout_view, name='buy_now_checkout'),
    path('cart/', user_views.cart_page_view, name='cart_page'),
    path('cart/remove/<int:product_id>/', remove_from_cart_view, name='remove_from_cart'),
    path('mini-cart-data/', user_views.mini_cart_data, name='mini_cart_data'),
    path('checkout/', user_views.checkout_view, name='checkout'),
    path('payment/success/', user_views.payment_success_view, name='payment_success'),
    path('payment/failed/', user_views.payment_failed_view, name='payment_failed'),
    path('notify-me/<int:product_id>/', notify_me_view, name='notify_me'),

    # --- Wishlist ---
    path('wishlist/', wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:product_id>/', toggle_wishlist, name='toggle_wishlist'),
    path('wishlist/remove/<int:product_id>/', remove_from_wishlist, name='remove_from_wishlist'),

    # --- User Profile & Address Management ---
    path('profile/', user_views.my_profile, name='my_profile'),
    path('profile/update/', update_profile, name='update_profile'),
    path('contact/send/', user_views.send_contact_email, name='send_contact_email'),
    path('order/<int:order_id>/view/', user_views.view_order_view, name='view_order'),
    path('order/<int:order_id>/cancel/', user_views.cancel_order_view, name='cancel_order'),
    # REMOVED old return_order_view URL, it's replaced by AJAX modal interaction
    # path('order/<int:order_id>/return/', user_views.return_order_view, name='return_order'), 
    path('order/<int:order_id>/', user_views.view_order_view, name='view_order'),
    path('order/<int:order_id>/invoice/', user_views.download_invoice_view, name='download_invoice'),
    path('checkout/coupons/', user_views.user_coupon_list_view, name='coupon_list'),
    path('checkout/remove-coupon/', user_views.remove_applied_coupon, name='remove_applied_coupon'),
    
    # UPDATED/NEW: User Return Request API Endpoints
    # The submit_return_request you provided earlier (now renamed for clarity)
    # path('return-request/', user_views.submit_return_request, name='submit_return_request'), # Old
    path('api/orders/<int:order_id>/return-items/', get_order_items_for_return, name='get_order_items_for_return_api'), # NEW
    path('api/returns/create/', create_return_request, name='create_return_request_api'), # NEW


    path('profile/address/add/', add_address_form_view, name='add_address_form'),
    path('api/profile/address/add/', add_address, name='add_address_api'),

    path('profile/address/<int:address_id>/edit/', edit_address, name='edit_address'),
    path('profile/address/<int:address_id>/set-default/', set_default_address, name='set_default_address'),
    path('profile/address/<int:address_id>/delete/', delete_address, name='delete_address'),
    path('api/addresses/<int:address_id>/', get_address_data, name='get_address_data'),

    # --- Newsletter Subscription ---
    path('subscribe/', subscribe_newsletter, name='subscribe_newsletter'),

    # --- Social Login ---
    path('accounts/', include('allauth.urls')),

    path('newsletter/', admin_views.NewsletterCampaignListView.as_view(), name='admin_newsletter_campaign_list'),
    path('newsletter/create/', admin_views.NewsletterCampaignCreateView.as_view(), name='admin_newsletter_campaign_create'),
    path('newsletter/<int:pk>/edit/', admin_views.NewsletterCampaignUpdateView.as_view(), name='admin_newsletter_campaign_edit'),
    path('newsletter/<int:pk>/delete/', admin_views.NewsletterCampaignDeleteView.as_view(), name='admin_newsletter_campaign_delete'),
    path('newsletter/<int:pk>/send/', admin_views.send_newsletter_campaign_view, name='admin_newsletter_campaign_send'),


    # Email Template management
    path('newsletter/templates/', admin_views.EmailTemplateListView.as_view(), name='admin_email_template_list'),
    path('newsletter/templates/create/', admin_views.EmailTemplateCreateView.as_view(), name='admin_email_template_create'),
    path('newsletter/templates/<int:pk>/edit/', admin_views.EmailTemplateUpdateView.as_view(), name='admin_email_template_edit'),
    path('newsletter/templates/<int:pk>/delete/', admin_views.EmailTemplateDeleteView.as_view(), name='admin_email_template_delete'),
    path('newsletter/templates/<int:pk>/preview/', admin_views.email_template_preview, name='admin_email_template_preview'),
]


# Media files for development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)