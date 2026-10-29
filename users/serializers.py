import json
import uuid

from django.contrib.auth.models import Group
from rest_framework import serializers

from users.models import User, Institution, DienstModell, get_default_expiry, Abrechnung


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ('name',)

class RegisterSerializer(serializers.ModelSerializer):

    def update(self, instance, validated_data):
        validated_data.pop('password',None)
        instance.save()
        return instance

    def validate(self, data):
        try:
            email = data.get('email')
            if email:
                data.update({'email': email.lower()})
            username=data.get('username')
            if not username:
                if not email:
                    raise serializers.ValidationError(("Neither username nor email provided"))
                username = str(uuid.uuid4())
                data.update({'username': username})
            user = User.objects.filter(username=username)
            email = User.objects.filter(email=email)
            if (len(user) > 0) | (len(email) > 0):
                raise serializers.ValidationError(("Username or email already exists"))
        except User.DoesNotExist:
            pass
        return data

    def create(self, validated_data, instance=None):
        """
        Jeder neu angemeldete Benutzer wird automatisch der Basic-Gruppe zugeordnet
        """
        user = User.objects.create(**validated_data)
        user.set_password(validated_data['password'])
        user.groups.add(Group.objects.get(name__exact='Basic'))
        user.save()
        return user

    class Meta:
        model = User
        fields = '__all__'




class InstitutionSerializer(serializers.ModelSerializer):

    class Meta:
        model = Institution
        # exclude = ['codename']
        # to have codename excluded changes need to be made in Mitarbeiterzuordnung and Profile
        fields = '__all__'

class AbrechnungSerializer(serializers.ModelSerializer):

    class Meta:
        model = Abrechnung
        fields = '__all__'

    def validate(self, data):
        try:
            stunden = data.get('data_stunden_encrypted')
            if stunden:
                for datum in stunden.keys():
                    stunden[datum]['VertragsArbeitszeiten']=float(stunden[datum]['VertragsArbeitszeiten'])
                    stunden[datum]['ReelleArbeitsstunden']=float(stunden[datum]['ReelleArbeitsstunden'])
                data.update({'data_stunden_encrypted': stunden})
        except Exception as e:
            raise serializers.ValidationError(e)
        return data

class DienstModellSerializer(serializers.ModelSerializer):

    class Meta:
        model = DienstModell
        fields = '__all__'


class UserSerializer(serializers.ModelSerializer):
    groups = GroupSerializer(many=True)
    institution_link = InstitutionSerializer(many=False)
    physician_groups_of_my_institution = serializers.SerializerMethodField('getphysiciansofmyinstitution')

    class Meta:
        model = User
        exclude = ['pi_passkey']

    def getphysiciansofmyinstitution(self,user):
        physicians = user.get_physician_groups_of_my_institution()
        return physicians

class MinimalUserSerializer(serializers.ModelSerializer):
    groups = GroupSerializer(many=True)

    class Meta:
        model = User
        fields = ['username','first_name','last_name','email','groups','is_emailvalidated','data_dienzt_preferences','pi_status']



class ChangePasswordSerializer(serializers.Serializer):
    model = User

    """
    Serializer for password change endpoint.
    """
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)

class ForgotPasswordSerializer(serializers.ModelSerializer):
    def update(self, instance, validated_data):
        instance.set_password(validated_data['password'])
        instance.is_emailvalidated=True
        instance.emailtoken=''
        instance.save()
        return instance

    def validate(self, data):
        token = data.get('emailtoken')
        user = User.objects.filter(username=data.get('username'), emailtoken=token)
        if (len(user)==0):
            raise serializers.ValidationError({"emailtoken": "Emailtoken do not match or user does not exist"})
        return data

    class Meta:
        model = User
        fields = ('username','password','emailtoken','is_emailvalidated')



