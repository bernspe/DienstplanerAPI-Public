from rest_framework import serializers

from dienstplan.models import WishData, DienstplanData, DienstTrade
from users.serializers import MinimalUserSerializer


class WishSerializer(serializers.ModelSerializer):
    class Meta:
        model = WishData
        fields = '__all__'

    def create(self, validated_data, instance=None):
        """
        Jedem neu angelgten Wunsch wird automatisch yearmonth berechnet und in der Methode auch gespeichert
        """
        wish = WishData.objects.create(**validated_data)
        _ = wish.getYearMonth()
        return wish


class DienstplanSerializer(serializers.ModelSerializer):
    creator_lastname = serializers.SerializerMethodField('getcreatorlastname')
    cocreator_lastnames = serializers.SerializerMethodField('getcocreatorslastnames')
    not_the_newest = serializers.SerializerMethodField('isnotthenewest')
    class Meta:
        model = DienstplanData
        fields = '__all__'
        read_only_fields = ['creator','creator_lastname']

    def getcreatorlastname(self, dpl):
        if dpl.creator is not None:
            return dpl.creator.last_name

    def getcocreatorslastnames(self, dpl):
        if dpl.cocreators.exists():
            return [d.last_name for d in dpl.cocreators.all()]

    def isnotthenewest(self,dpl):
        rdate = dpl.referringDate
        inst = dpl.institution_link
        lmod = dpl.last_modified
        q = DienstplanData.objects.filter(institution_link=inst, referringDate=rdate,status='Skizze',last_modified__gt=lmod)
        return len(q)>0

class ConnectedDienstTradeSerializer(serializers.ModelSerializer):
    expose_user_name = serializers.SerializerMethodField('getexposeuserfullname')
    takeover_user_name = serializers.SerializerMethodField('gettakeoverusername')
    class Meta:
        model = DienstTrade
        fields = '__all__'
        read_only_fields = ['creator']

    def getexposeuserfullname(self, trade):
        if trade.expose_user is not None:
            return trade.expose_user.last_name + ', ' + trade.expose_user.first_name

    def gettakeoverusername(self,trade):
        if trade.takeover_user is not None:
            return trade.takeover_user.last_name + ', ' + trade.takeover_user.first_name

class DienstTradeSerializer(serializers.ModelSerializer):
    connected_trades = ConnectedDienstTradeSerializer(many=True, read_only=True)
    expose_user_name = serializers.SerializerMethodField('getexposeuserfullname')
    takeover_user_name = serializers.SerializerMethodField('gettakeoverusername')
    class Meta:
        model = DienstTrade
        fields = '__all__'
        read_only_fields = ['creator']

    def getexposeuserfullname(self, trade):
        if trade.expose_user is not None:
            return trade.expose_user.last_name + ', ' + trade.expose_user.first_name

    def gettakeoverusername(self,trade):
        if trade.takeover_user is not None:
            return trade.takeover_user.last_name + ', ' + trade.takeover_user.first_name
