from django import forms
from django.forms.widgets import ClearableFileInput, DateTimeInput # Import DateTimeInput
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User # You might use get_user_model() instead of direct User import
from django.contrib.auth import get_user_model
from core.models import Product, Coupon, EmailTemplate, NewsletterCampaign # Ensure Coupon is imported
import uuid
# Import the actual Address model and UserProfile
from core.models import UserProfile, Address, NewsletterSubscriber # Import NewsletterSubscriber
from core.models import OfferBanner, Offer, Product, Category


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        fields = ['full_name', 'phone', 'house_name', 'street', 'city', 'state', 'pincode', 'country', 'is_default']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'phone': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'house_name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'street': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'city': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'state': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'pincode': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'country': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-md'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'mr-2'}), # Added widget for checkbox
        }

class NewsletterForm(forms.ModelForm):
    class Meta:
        model = NewsletterSubscriber
        fields = ['email']
        widgets = {
            'email': forms.EmailInput(attrs={'placeholder': 'email@example.com', 'class': 'flex-grow px-5 py-3 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-400 w-full sm:w-auto'})
        }


# --- Login Form ---
class UserLoginForm(AuthenticationForm):
    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        self.fields['username'].widget = forms.TextInput(attrs={
            'placeholder': 'Email (or Username)',
            'class': 'w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400'
        })
        self.fields['password'].widget = forms.PasswordInput(attrs={
            'placeholder': 'Password',
            'class': 'w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400'
        })

# --- Registration Form ---
class UserRegistrationForm(UserCreationForm):
    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'First Name', 'class': 'form-input'})
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Last Name', 'class': 'form-input'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'Email', 'class': 'form-input'})
    )
    phone_number = forms.CharField( # This field is not saved to User model directly by default
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Phone Number', 'class': 'form-input'})
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={'placeholder': 'Password', 'class': 'form-input'})
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm Password', 'class': 'form-input'})
    )

    class Meta:
        model = get_user_model() # Use get_user_model() here
        fields = ['first_name', 'last_name', 'email', 'password1', 'password2']

    def clean_email(self):
        email = self.cleaned_data['email']
        UserModel = get_user_model()
        if UserModel.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.email = self.cleaned_data["email"]
        user.username = self.cleaned_data["email"] # Use email as username for consistency
        if commit:
            user.save()
        return user

# --- Product Form ---
class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'category', 'price', 'size', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400'}),
            'category': forms.Select(attrs={'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400'}),
            'price': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400'}),
            'size': forms.TextInput(attrs={'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400'}),
            'description': forms.Textarea(attrs={'class': 'w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400', 'rows': 4}),
        }

# UPDATED: Coupon Form with new fields and datetime widgets
class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = [
            'code', 'discount', 'min_purchase', 'valid_from', 'valid_to',
            'usage_limit',
            'min_orders_for_user',
            'min_unique_products_in_cart',
            'min_total_items_in_cart','is_active','applies_to_new_users_only',
        ]
        widgets = {
            'valid_from': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'valid_to': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            # Add widgets for new fields if you want specific styling
            'code': forms.TextInput(attrs={'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'discount': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'min_purchase': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'usage_limit': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'applies_to_new_users_only': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'min_orders_for_user': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'min_unique_products_in_cart': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'min_total_items_in_cart': forms.NumberInput(attrs={'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }
        help_texts = {
            'min_orders_for_user': 'Minimum number of successful (Delivered/Returned) orders a user must have to use this coupon (0 for no minimum).',
            'min_unique_products_in_cart': 'Minimum number of different products in the cart to use this coupon (0 for no minimum).',
            'min_total_items_in_cart': 'Minimum total quantity of all items in the cart to use this coupon (0 for no minimum).',
            'usage_limit': 'Maximum number of times this coupon can be used (0 for unlimited).',
        }

    def clean(self):
        cleaned_data = super().clean()
        applies_to_new_users_only = cleaned_data.get('applies_to_new_users_only')
        min_orders_for_user = cleaned_data.get('min_orders_for_user')

        # Custom validation: Cannot be for new users AND require minimum orders
        if applies_to_new_users_only and min_orders_for_user and min_orders_for_user > 0:
            self.add_error('min_orders_for_user', "Cannot set 'Minimum Orders for User' if 'Applies to New Users Only' is checked.")
            self.add_error('applies_to_new_users_only', "Cannot apply to new users only if a minimum order count is specified.")

        return cleaned_data

# --- MultiFile Input Widget ---
class MultiFileInput(ClearableFileInput):
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        final_attrs = {'multiple': True}
        if attrs:
            final_attrs.update(attrs)
        super().__init__(attrs=final_attrs)

# --- Batch Upload Form ---
class BatchUploadForm(forms.Form):
    product_file = forms.FileField(
        required=False,
        label="Product Details File (.txt)"
    )
    images = forms.FileField(
        widget=MultiFileInput(),
        required=False,
        label="Product Images (Multiple)"
    )

class EmailTemplateForm(forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ['name', 'subject', 'html_content', 'plain_content'] # Added plain_content back
        widgets = {
            'html_content': forms.Textarea(attrs={'rows': 20}), # Make textarea larger
            'plain_content': forms.Textarea(attrs={'rows': 10}),
        }

class NewsletterCampaignForm(forms.ModelForm):
    class Meta:
        model = NewsletterCampaign
        fields = [
            'title',
            'email_template',
            'recipients_type',
            'custom_recipient_emails',
            'scheduled_at',
            'status',
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500',
                'placeholder': 'Enter campaign title'
            }),
            'email_template': forms.HiddenInput(), # This should remain hidden as it's selected via JS/cards
            'recipients_type': forms.Select(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500',
            }),
            'custom_recipient_emails': forms.Textarea(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500',
                'rows': 5,
                'placeholder': 'Comma-separated emails, e.g., email1@example.com, email2@example.com'
            }),
            'scheduled_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local', # Ensure the HTML input type is datetime-local for native picker
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500',
            }),
            'status': forms.Select(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email_template'].empty_label = "-- Select Template --"

    def clean(self):
        cleaned_data = super().clean()
        recipients_type = cleaned_data.get('recipients_type')
        custom_recipient_emails = cleaned_data.get('custom_recipient_emails')

        if recipients_type == 'custom_list' and not custom_recipient_emails:
            self.add_error('custom_recipient_emails', "Custom recipient emails are required if 'Custom Email List' is selected.")
        return cleaned_data


class OfferBannerForm(forms.ModelForm):
    """
    Form for managing the OfferBanner (scrolling flash message).
    """
    class Meta:
        model = OfferBanner
        fields = ['text_content', 'text_color', 'bg_color', 'is_active']
        widgets = {
            # Consider using a color picker widget if you integrate one (e.g., django-colorfield)
            # For now, simple text input for hex codes
            'text_color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control'}),
            'bg_color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control'}),
            'text_content': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter banner text'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'})
        }
        labels = {
            'text_content': 'Banner Text',
            'text_color': 'Text Color',
            'bg_color': 'Background Color',
            'is_active': 'Enable Banner'
        }

class OfferForm(forms.ModelForm):
    class Meta:
        model = Offer
        fields = [
            'name', 'tag_text', 'discount_percentage', 'discount_amount',
            'start_date', 'end_date', 'is_active',
            'background_color', 'text_color'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Summer Sale'}),
            'tag_text': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., SALE, 20% OFF'}),
            'discount_percentage': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g., 15.00'}),
            'discount_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'placeholder': 'e.g., 10.00'}),
            'start_date': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'end_date': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'background_color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}), # Keep type="color"
            'text_color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}),     # Keep type="color"
        }
        labels = {
            'name': 'Offer Name',
            'tag_text': 'Product Tag Text',
            'discount_percentage': 'Discount Percentage (%)',
            'discount_amount': 'Discount Amount ($)',
            'start_date': 'Start Date & Time',
            'end_date': 'End Date & Time',
            'is_active': 'Offer Active',
            'background_color': 'Tag Background Color',
            'text_color': 'Tag Text Color',
        }

    def clean(self):
        cleaned_data = super().clean()
        discount_percentage = cleaned_data.get('discount_percentage')
        discount_amount = cleaned_data.get('discount_amount')
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if discount_percentage is not None and discount_amount is not None:
            raise forms.ValidationError("An offer cannot have both a percentage and a fixed amount discount. Choose one.")
        if discount_percentage is None and discount_amount is None:
            raise forms.ValidationError("An offer must have either a percentage or a fixed amount discount.")
        
        if discount_percentage is not None and (discount_percentage < 0 or discount_percentage > 100):
            raise forms.ValidationError("Discount percentage must be between 0 and 100.")
        if discount_amount is not None and discount_amount < 0:
            raise forms.ValidationError("Discount amount cannot be negative.")

        if start_date and end_date and start_date >= end_date:
            raise forms.ValidationError("End date must be after start date.")

        return cleaned_data
    
class ProductFilterForm(forms.Form):
    """
    Form for filtering and sorting products in the custom admin offer page.
    """
    query = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Search products...'}),
        label='Search'
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        empty_label="All Categories",
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Category'
    )
    sort_by = forms.ChoiceField(
        choices=[
            ('name', 'Name (A-Z)'),
            ('-name', 'Name (Z-A)'),
            ('price', 'Price (Low to High)'),
            ('-price', 'Price (High to Low)'),
            ('created_at', 'Oldest First'),
            ('-created_at', 'Newest First'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Sort By'
    )
    # Add a field to filter by whether product is currently in an offer
    in_offer = forms.ChoiceField(
        choices=[
            ('', 'All Products'),
            ('yes', 'Products with Offers'),
            ('no', 'Products without Offers'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Offer Status'
    )
