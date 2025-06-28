from django.contrib import admin
from django.db import models
from django.db.models import Sum
from decimal import Decimal

# Import your models, including NewsletterSubscriber
from .models import Product, Order, OrderItem, Address, Category, ProductImage, NewsletterSubscriber # Added NewsletterSubscriber

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    autocomplete_fields = ['product']
    fields = ('product',)

    readonly_fields = ('get_price_at_purchase_display',)

    def get_price_at_purchase_display(self, obj):
        return obj.price_at_purchase if obj.price_at_purchase is not None else 'N/A'

    get_price_at_purchase_display.short_description = 'Price at Purchase'


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'status', 'total_price', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['id', 'user__username']
    inlines = [OrderItemInline]
    readonly_fields = ('total_price',)

    def save_model(self, request, obj, form, change):

        super().save_model(request, obj, form, change)

        if change and 'status' in form.changed_data and obj.status == 'Delivered':
           for item in obj.items.all():
                if item.product and not item.product.is_sold:
                    item.product.is_sold = True
                    item.product.save() # Save the product to update its sold status

    def save_formset(self, request, form, formset, change):

        super().save_formset(request, form, formset, change)

        order = form.instance
        order.refresh_from_db()

        total_price_agg = order.items.aggregate(Sum('price_at_purchase'))['price_at_purchase__sum']

        order.total_price = total_price_agg if total_price_agg is not None else Decimal('0.00')

        order.save()


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'is_sold', 'created_at']
    list_filter = ['is_sold', 'category', 'created_at']
    search_fields = ['name']

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}

# NEW: Register NewsletterSubscriber
@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'subscribed_at')
    search_fields = ('email',)
    list_filter = ('subscribed_at',)


admin.site.register(Address)
admin.site.register(ProductImage)