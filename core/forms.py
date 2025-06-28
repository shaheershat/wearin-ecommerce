# core/forms.py
from django import forms
from django.forms.widgets import ClearableFileInput
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model
from core.models import Product, Coupon
import uuid
# Import the actual Address model and UserProfile
from core.models import UserProfile, Address, NewsletterSubscriber # Import NewsletterSubscriber


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class AddressForm(forms.ModelForm):
    # Changed model from UserProfile to Address
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

# --- NEW FORM FOR NEWSLETTER ---
class NewsletterForm(forms.ModelForm):
    class Meta:
        model = NewsletterSubscriber
        fields = ['email']
        widgets = {
            'email': forms.EmailInput(attrs={'placeholder': 'email@example.com', 'class': 'flex-grow px-5 py-3 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-400 w-full sm:w-auto'})
        }


# --- Rest of your forms.py remains the same ---
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
        model = User
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

# --- Coupon Form ---
class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = ['code', 'discount', 'limit', 'min_purchase', 'redeemable_price', 'valid_from', 'valid_to', 'is_active']
        widgets = {
            'valid_from': forms.DateInput(attrs={'type': 'date', 'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
            'valid_to': forms.DateInput(attrs={'type': 'date', 'class': 'w-full border border-gray-300 px-3 py-2 rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name not in ['valid_from', 'valid_to', 'is_active']:
                field.widget.attrs.update({'class': 'w-full border border-gray-300 px-3 py-2 rounded'})
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'form-checkbox'})

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