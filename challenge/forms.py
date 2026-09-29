from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()

DISPLAY_NAME_HELP = "Come ti vedranno gli altri (es. Manuel). Se vuoto si usa lo username."


class SignupForm(UserCreationForm):
    first_name = forms.CharField(
        label="Nome visualizzato",
        max_length=30,
        required=False,
        help_text=DISPLAY_NAME_HELP,
    )
    invite_code = forms.CharField(label="Codice d'invito", max_length=100)

    class Meta(UserCreationForm.Meta):
        fields = ("username", "first_name")

    def __init__(self, *args, require_code=True, **kwargs):
        super().__init__(*args, **kwargs)
        # Niente codice se non è configurato o se si arriva dal link d'invito di un gruppo
        if not settings.SIGNUP_CODE or not require_code:
            del self.fields["invite_code"]

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"].strip()
        if code != settings.SIGNUP_CODE:
            raise forms.ValidationError("Codice d'invito non valido.")
        return code


class ProfileForm(forms.ModelForm):
    """Modifica di nome visualizzato e username dalla pagina Profilo."""

    first_name = forms.CharField(
        label="Nome visualizzato", max_length=30, required=False, help_text=DISPLAY_NAME_HELP
    )

    class Meta:
        model = User
        fields = ("first_name", "username")
        help_texts = {"username": "Serve per accedere e per farti aggiungere dagli amici."}

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        # "Luca" e "luca" sarebbero indistinguibili quando un amico ti cerca
        if User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Questo username è già usato da qualcun altro.")
        return username
