from django.contrib import admin, messages
from django import forms
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, UserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.utils.translation import ngettext

from users.models import User, Institution, Abrechnung


class UserChangeForm(forms.ModelForm):
    """A form for updating users. Includes all the fields on
    the user, but replaces the password field with admin's
    disabled password hash display field.
    """

    password = ReadOnlyPasswordHashField()

    class Meta:
        model = User
        fields = "__all__"



class NewUserAdmin(UserAdmin):
    form = UserChangeForm
    list_display = ['first_name','last_name','email','get_groups','institution','is_emailvalidated','WAZ','DienstPrefs','is_staff']
    list_filter = ['institution_link']
    search_fields = ['last_name','email']
    readonly_fields = ('date_joined',)
    actions = ['send_test_email']

    fieldsets = UserAdmin.fieldsets + (
            (None, {'fields': ('pi_status','institution_link','allows_payment_data','data_encrypted','data_dienzt_preferences')}),
    )

    def send_test_email(self, request, queryset):
        sent=0
        for u in queryset:
            u.email_user('Test-Message',
                         "<h1>Test</h1><p>Wenn du das lesen kannst, ist alles korrekt</p><a href='https://dienzt.renecol.org'>Gehe zu DienZt</a>")
            sent+=1
        self.message_user(request, ngettext(
            '%d Email ist geschickt worden',
            '%d Emails sind geschickt worden',
            sent,
        ) % sent, messages.SUCCESS)

    send_test_email.short_description = "Test EMail senden"
    def get_groups(self, obj):
        return "\n".join([p.name for p in obj.groups.all()])

    def institution(self, obj):
        i = obj.institution_link
        if i:
            return i.name
        else:
            return ''

    def WAZ(self,obj):
        if obj.data_encrypted:
            if 'Vertrags_Arbeitszeiten' in obj.data_encrypted.keys():
                return sum([float(i) for i in obj.data_encrypted['Vertrags_Arbeitszeiten'].values()])
            else:
                return 'n.a.'
        else:
            return 'n.a.'

    def DienstPrefs(self,obj):
        if obj.data_dienzt_preferences:
            if 'dienstPreferences' in obj.data_dienzt_preferences.keys():
                return True
            else:
                return False
        else:
            return False

    DienstPrefs.boolean = True


class InstitutionAdmin(admin.ModelAdmin):
    list_display = ['name', 'created', 'expires']
    list_filter = ['created','expires']
    readonly_fields = ('id',)

class AbrechnungAdmin(admin.ModelAdmin):
    list_display = ['abrechnungowner','institution_link','yearmonth']
    list_filter = ['institution_link','yearmonth']
    readonly_fields = ('id',)

    def abrechnungowner(self, obj):
        u = obj.owner
        if u:
            return u.last_name+', '+u.first_name


# Now register the new UserAdmin...
admin.site.register(User, NewUserAdmin)
admin.site.register(Institution, InstitutionAdmin)
admin.site.register(Abrechnung, AbrechnungAdmin)
