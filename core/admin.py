from django.contrib import admin
from django.db import models
from django.db.models import Sum
from decimal import Decimal
from django.contrib import messages
from .models import Order, WalletTransaction, Wallet

# Import ALL your models
from .models import (
    Product, Order, OrderItem, Address, Category, ProductImage,
    NewsletterSubscriber, EmailOTP, OTP, UserProfile, Coupon,
    Cart, CartItem, Wishlist # Added missing models
)

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    autocomplete_fields = ['product']
    fields = ('product',)

    readonly_fields = ('get_price_at_purchase_display',)

    def get_price_at_purchase_display(self, obj):
        return obj.price_at_purchase if obj.price_at_purchase is not None else 'N/A'

    get_price_at_purchase_display.short_description = 'Price at Purchase'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'is_sold', 'created_at']
    list_filter = ['is_sold', 'category', 'created_at']
    search_fields = ['name']

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'subscribed_at')
    search_fields = ('email',)
    list_filter = ('subscribed_at',)

# --- NEW REGISTRATIONS FOR UNREGISTERED MODELS ---

@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ('email', 'otp', 'purpose', 'created_at')
    list_filter = ('purpose', 'created_at')
    search_fields = ('email', 'otp')
    readonly_fields = ('created_at',) # OTPs are usually not edited manually

@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'code', 'purpose', 'created_at', 'is_expired')
    list_filter = ('purpose', 'created_at')
    search_fields = ('user__username', 'code')
    readonly_fields = ('created_at',)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user',) # Display the associated user
    search_fields = ('user__username', 'user__email')

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount', 'limit', 'min_purchase', 'valid_from', 'valid_to', 'is_active')
    list_filter = ('is_active', 'valid_from', 'valid_to')
    search_fields = ('code',)

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('user',)
    search_fields = ('user__username',)

@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('cart', 'product')
    list_filter = ('cart__user',)
    search_fields = ('cart__user__username', 'product__name')

@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ('user', 'product')
    list_filter = ('user',)
    search_fields = ('user__username', 'product__name')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'status', 'return_status', 'total_price', 'created_at')
    list_filter = ('status', 'return_status')
    actions = ['approve_return', 'reject_return']
    inlines = [OrderItemInline]
    readonly_fields = ('total_price',)

    def approve_return(self, request, queryset):
        count = 0
        for order in queryset:
            if order.return_status == 'Requested':
                order.return_status = 'Approved'
                order.status = 'Returned'
                order.save()

                wallet, _ = Wallet.objects.get_or_create(user=order.user)
                wallet.credit(order.total_price)

                WalletTransaction.objects.create(
                    wallet=wallet,
                    transaction_type='refund',
                    amount=order.total_price,
                    reason=f"Refund for returned Order #{order.id}"
                )

                count += 1
        self.message_user(request, f"{count} return(s) approved and refunded.", level=messages.SUCCESS)

    def reject_return(self, request, queryset):
        count = 0
        for order in queryset:
            if order.return_status == 'Requested':
                order.return_status = 'Rejected'
                order.save()
                count += 1
        self.message_user(request, f"{count} return(s) rejected.", level=messages.WARNING)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and 'status' in form.changed_data and obj.status == 'Delivered':
            for item in obj.items.all():
                if item.product and not item.product.is_sold:
                    item.product.is_sold = True
                    item.product.save()

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        order = form.instance
        order.refresh_from_db()
        total_price_agg = order.items.aggregate(Sum('price_at_purchase'))['price_at_purchase__sum']
        order.total_price = total_price_agg if total_price_agg is not None else Decimal('0.00')
        order.save()

# Models that don't need custom Admin classes (default display is fine)
admin.site.register(Address)
admin.site.register(ProductImage)
