from django.contrib import admin
from django.db import models
from django.db.models import Sum
from decimal import Decimal
from django.contrib import messages

from .models import (
    Product, Order, OrderItem, Address, Category, ProductImage,
    NewsletterSubscriber, EmailOTP, OTP, UserProfile, Coupon,
    Cart, CartItem, Wishlist, EmailTemplate, NewsletterCampaign, NewsletterSubscription,
    ReturnRequest, ReturnReason, ReturnItem, Wallet, WalletTransaction
)

from .forms import ProductForm, CouponForm, EmailTemplateForm, NewsletterCampaignForm


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('product', 'order', 'quantity', 'price_at_purchase')
    search_fields = ('product__name', 'order__custom_order_id')
    readonly_fields = ('price_at_purchase',)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    autocomplete_fields = ['product']
    fields = ('product', 'quantity', 'price_at_purchase', 'original_total_price', 'discount_amount') 
    readonly_fields = ('price_at_purchase', 'original_total_price', 'discount_amount',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'is_sold', 'created_at', 'stock_quantity']
    list_filter = ['is_sold', 'category', 'created_at']
    search_fields = ['name']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ('email', 'otp', 'purpose', 'created_at')
    list_filter = ('purpose', 'created_at')
    search_fields = ('email', 'otp')
    readonly_fields = ('created_at',)


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'code', 'purpose', 'created_at', 'is_expired')
    list_filter = ('purpose', 'created_at')
    search_fields = ('user__username', 'code')
    readonly_fields = ('created_at',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user',)
    search_fields = ('user__username', 'user__email')


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'discount', 'min_purchase', 'valid_from', 'valid_to',
        'is_active', 'usage_limit', 'used_count',
        'applies_to_new_users_only', 'min_orders_for_user',
        'min_unique_products_in_cart', 'min_total_items_in_cart'
    )
    list_filter = (
        'is_active', 'valid_from', 'valid_to',
        'applies_to_new_users_only', 'min_orders_for_user'
    )
    search_fields = ('code',)
    form = CouponForm

    fieldsets = (
        (None, {
            'fields': ('code', 'discount', 'is_active', 'usage_limit', 'used_count'),
        }),
        ('Validity Period', {
            'fields': ('valid_from', 'valid_to'),
            'description': 'Set the dates and times when this coupon is valid.'
        }),
        ('Minimum Requirements', {
            'fields': ('min_purchase', 'min_unique_products_in_cart', 'min_total_items_in_cart'),
            'description': 'Conditions based on cart contents.'
        }),
        ('User Specific Conditions', {
            'fields': ('applies_to_new_users_only', 'min_orders_for_user'),
            'description': 'Conditions based on user history. Cannot apply to new users AND require minimum orders.'
        }),
    )

    readonly_fields = ('used_count',)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'updated_at',)
    search_fields = ('user__username',)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('cart', 'product', 'quantity', 'is_reserved', 'reserved_until',)
    list_filter = ('cart__user', 'is_reserved',)
    search_fields = ('cart__user__username', 'product__name')


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'added_at',)
    list_filter = ('user',)
    search_fields = ('user__username', 'product__name')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('custom_order_id', 'user', 'status', 'total_amount', 'payment_status', 'payment_method', 'created_at')
    list_filter = ('status', 'payment_status', 'payment_method', 'created_at')
    search_fields = ('custom_order_id', 'user__username', 'user__email')
    inlines = [OrderItemInline]
    readonly_fields = (
        'user', 'address', 
        'total_amount', 'coupon_discount', 'shipping_charge', 
        'razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature',
        'created_at', 'updated_at', 'custom_order_id'
    )

    fieldsets = (
        (None, {
            'fields': ('user', 'address', 'custom_order_id',),
        }),
        ('Payment Details', {
            'fields': ('total_amount', 'coupon_discount', 'shipping_charge', 'payment_method', 'payment_status', 'razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature'),
        }),
        ('Order Status', {
            'fields': ('status',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


    def save_model(self, request, obj, form, change):
        if change and 'status' in form.changed_data and obj.status == 'Delivered':
            for item in obj.items.all():
                if item.product and not item.product.is_sold:
                    item.product.is_sold = True
                    item.product.save()

                    from core.models import CartItem 
                    CartItem.objects.filter(product=item.product).delete()
        
        super().save_model(request, obj, form, change)


    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        order = form.instance
        order.refresh_from_db()
        total_price_agg = order.items.aggregate(
            calculated_total=Sum(models.F('quantity') * models.F('price_at_purchase'))
        )['calculated_total']
        
        order.total_amount = total_price_agg if total_price_agg is not None else Decimal('0.00')
        order.save()


admin.site.register(Address)
admin.site.register(ProductImage)
admin.site.register(Wallet)
admin.site.register(WalletTransaction)


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'created_at', 'updated_at')
    search_fields = ('name', 'subject')
    list_filter = ('created_at',)


@admin.register(NewsletterCampaign)
class NewsletterCampaignAdmin(admin.ModelAdmin):
    list_display = ('title', 'email_template', 'status', 'scheduled_at', 'sent_at', 'sent_by', 'sent_count_display')
    list_filter = ('status', 'scheduled_at', 'recipients_type')
    search_fields = ('title', 'email_template__name')
    date_hierarchy = 'created_at'
    raw_id_fields = ('email_template', 'sent_by')

    fieldsets = (
        (None, {
            'fields': ('title', 'email_template', 'recipients_type', 'custom_recipient_emails'),
        }),
        ('Scheduling & Status', {
            'fields': ('scheduled_at', 'status', 'sent_by', 'sent_at', 'total_recipients'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Sent Count')
    def sent_count_display(self, obj):
        return obj.total_recipients


@admin.register(NewsletterSubscription)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('email',)
    ordering = ('-created_at',)


@admin.register(ReturnReason)
class ReturnReasonAdmin(admin.ModelAdmin):
    list_display = ('reason_text', 'requires_custom_input')
    search_fields = ('reason_text',)


class ReturnItemInlineForReturnRequest(admin.TabularInline):
    model = ReturnItem
    extra = 0
    fields = ('order_item', 'quantity',)
    readonly_fields = ('order_item',)
    autocomplete_fields = ('order_item',)


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'order_custom_id_display', 'reason_display', 'status', 'requested_at', 'reviewed_at',)
    list_filter = ('status', 'requested_at', 'reason',)
    search_fields = ('user__username', 'order__custom_order_id', 'custom_reason')
    date_hierarchy = 'requested_at'
    inlines = [ReturnItemInlineForReturnRequest]
    readonly_fields = ('user', 'order', 'requested_at', 'reviewed_at',)

    fieldsets = (
        (None, {
            'fields': ('user', 'order', 'status', 'requested_at', 'reviewed_at', 'admin_notes',)
        }),
        ('Return Details', {
            'fields': ('reason', 'custom_reason',)
        }),
    )

    @admin.display(description='Order ID')
    def order_custom_id_display(self, obj):
        return obj.order.custom_order_id if obj.order else 'N/A'

    @admin.display(description='Reason')
    def reason_display(self, obj):
        return obj.reason.reason_text if obj.reason else 'N/A'