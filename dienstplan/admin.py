from importlib.resources._common import _

from django.contrib import admin

from dienstplan.models import DienstplanData, WishData, DienstTrade


class InstitutionListFilter(admin.SimpleListFilter):
    # Human-readable title which will be displayed in the
    # right admin sidebar just above the filter options.
    title = _("Institution")

    # Parameter for the filter that will be used in the URL query.
    parameter_name = "institution_link"


class DienstplanAdmin(admin.ModelAdmin):
    list_display = ['referringDate', 'status', 'institution','created']
    list_filter= ['institution_link','referringDate','status','created']
    def institution(self, obj):
        i = obj.institution_link
        if i:
            return i.name
        else:
            return ''



class WishDataAdmin(admin.ModelAdmin):
    list_display = ['wishowner','institution','timespan','yearmonth','created']
    list_filter = ['created','owner__institution_link','yearmonth']
    search_fields = ['owner__last_name']
    def wishowner(self, obj):
        u = obj.owner
        if u:
            return u.last_name+', '+u.first_name

    def institution(self, obj):
        u = obj.owner
        if u:
            i = u.institution_link
            if i:
                return i.name

    def timespan(self, obj):
        min, max = obj.getTimeConstraints()
        return min.strftime('%d.%m.%y')+'-'+max.strftime('%d.%m.%y')

class DienstTradeAdmin(admin.ModelAdmin):
    list_display = ['referringDate', 'status', 'institution','exposer', 'takeover' ,'created']
    list_filter= ['institution_link','referringDate','status','created']
    def institution(self, obj):
        i = obj.institution_link
        if i:
            return i.name
        else:
            return ''

    def exposer(self,obj):
        u = obj.expose_user
        if u:
            return u.last_name+', '+u.first_name
        else:
            return ''

    def takeover(self,obj):
        u = obj.takeover_user
        if u:
            return u.last_name+', '+u.first_name
        else:
            return ''

admin.site.register(DienstplanData, DienstplanAdmin)
admin.site.register(WishData, WishDataAdmin)
admin.site.register(DienstTrade, DienstTradeAdmin)

