import random
import time
from datetime import datetime, timezone, timedelta
import json
import re

import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.http import HttpResponse
from django.template.loader import get_template
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.models import AccessToken, RefreshToken
from oauth2_provider.oauth2_validators import Application
from oauth2_provider.views import TokenView as TV
from oauth2_provider.settings import oauth2_settings
from oauth2_provider.views.mixins import OAuthLibMixin
from oauthlib import common
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, action, permission_classes
from rest_framework.generics import get_object_or_404, UpdateAPIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from DienstplanerAPI.settings import ADMINISTRATION_GROUPS, BASIC_GROUPS, PRIVACYIDEA_HOST, DIENZT_REALM, PI_OTP_PIN
from dienstplan.utils import automake_dienstplan_for_institution
from users.models import User, Institution, DienstModell, Abrechnung
from users.permissions import IsOwner, IsMyInstitution, IsMyInstitutionsDienstModell, IsVerwaltung, IsPlaner, \
    IsVerwaltungAndUserNotEmailValidated
from users.serializers import UserSerializer, RegisterSerializer, InstitutionSerializer, \
    DienstModellSerializer, MinimalUserSerializer, ChangePasswordSerializer, ForgotPasswordSerializer, \
    AbrechnungSerializer


def hasExpired(expiry):
    present = datetime.now().replace(tzinfo=timezone.utc)
    return present > expiry


# Create your views here.

class UserViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing user instances.
    """
    queryset = User.objects.all()
    authentication_classes = [OAuth2Authentication]

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action == 'list':
            permission_classes = [IsAdminUser]
        elif self.action == 'destroy':
            permission_classes = [IsAdminUser | IsOwner | IsVerwaltungAndUserNotEmailValidated]
        else:
            permission_classes = [IsAuthenticated, IsOwner]
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        return UserSerializer

    def partial_update(self, request, pk=None, *args, **kwargs):
        user = User.objects.get(username=pk)
        serializer = self.get_serializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    serializer.save()
                    return Response(serializer.data, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs):
        queryset = User.objects.all()
        user = get_object_or_404(queryset, pk=pk)
        serializer = self.get_serializer(user)
        return Response(serializer.data)

    @action(methods=['post'], detail=True)
    @permission_classes([IsAuthenticated])
    def issue_second_token(self, request, pk=None):
        """
        Method roll out token to authenticated user for 2fa
        1. Auth at PI server with automatic password from login data, saved in pi_passkey (SHA1)
        2. Roll out token and return it
        :param request:
        :param pk:
        :return:
        """
        try:
            user = User.objects.get(username=pk)
            user.pi_status='TRIGGERED'
            user.save()
            tokenuri = PRIVACYIDEA_HOST + 'token/init'
            auth_status, response_after_auth = user.auth_at_privacyidea()
            if auth_status == 200:
                # request for token rollout
                params = {
                    "timeStep": 30,
                    "otplen": 6,
                    "genkey": True,
                    "type": "hotp",
                    "hashlib": "sha1",
                    "radius.system_settings": True,
                    "2stepinit": False,
                    "rollover": False,
                    "description": "Token rolled out by API call",
                    "validity_period_start": "",
                    "validity_period_end": "",
                    "pin": PI_OTP_PIN,
                    "user": user.email,
                    "realm": "dienzt"
                }
                headers = {
                    'Pi-Authorization': response_after_auth['result']['value']['token'],
                    'Content-Type': 'application/json',
                    'Content-Length': '0',
                    "Accept-Encoding": "identity, deflate, compress, gzip",
                    "Connection": "keep-alive",
                    "Accept": "*/*",
                    "User-Agent": "python-requests",
                }
                r = requests.post(tokenuri, headers=headers, data=json.dumps(params))
                print('Yield Token response %i' % r.status_code)
                if (r.status_code == 200):
                    user.delete_current_pi_token()
                    user.pi_status='2FA'
                    rdata = json.loads(r.content)
                    user.pi_serialnumber=rdata['detail']['serial']
                    user.save()
                    t = get_template('user/security-2fa-issued.html')
                    ts = str(datetime.now(timezone.utc))
                    user.email_user('Sicherheitshinweis: 2Faktor-Authentication' + ts, t.render(context={
                        'Firstname': user.first_name,'random': str(random.randint(0, 9))}))
                    return HttpResponse(r.content, content_type='application/json')
                else:
                    return Response({'error': 'Token rollout failed'}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'error': 'User auth failed'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)



    @action(methods=['post'], detail=True)
    @permission_classes([IsAuthenticated])
    def validate_second_token(self, request, pk=None):
        """
        Validates the 2FA Token against PI endpoint
        :param request: body contains the token
        :param pk:
        :return:
        """
        try:
            user = User.objects.get(username=pk)
            token = request.data['token']
            r = user.validate_mfa_token(token)
            if (r.status_code == 200):
                user.pi_status = 'VALID'
                user.save()
                return HttpResponse(r.content, content_type='application/json')
            else:
                return Response({'error': 'Token validation failed'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True)
    @permission_classes([IsAuthenticated, IsAdminUser | IsPlaner | IsVerwaltung])
    def delete_second_token(self, request, pk=None):
        """
        Deletes all 2FA OTP of a user
        :param request:
        :param pk: username of the respective user
        :return:
        """
        try:
            user_to_delete_2fa = User.objects.get(username=pk)
            operating_user = request.user
            s, response_after_auth = user_to_delete_2fa.auth_at_privacyidea()
            if s == 200:
                del_reponse=user_to_delete_2fa.delete_current_pi_token(token=response_after_auth['result']['value']['token'])
                print(del_reponse)
                return HttpResponse(del_reponse.content, content_type='application/json')
            else:
                return Response({'error': 'Auth at PI Server failed'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


    @action(methods=['post'], detail=True)
    @permission_classes([IsAuthenticated])
    def set_new_email(self, request, pk=None):
        """
        Set new EMail after checking token
        :param
        :return: user object
        """
        data = request.data
        emailtoken = data['emailtoken']
        email = data['email']
        if len(emailtoken) > 0:
            try:
                user = User.objects.get(username=pk, emailtoken=emailtoken)
                # check if email already exists
                email_try = User.objects.filter(email=email)
                if len(email_try) > 0:
                    return Response({'error': 'Email already in use'},
                                    status=status.HTTP_400_BAD_REQUEST)
                # send a notification to the user that EMail address is about to change
                t = get_template('user/email-about-to-change.html')
                ts = str(datetime.now(timezone.utc))
                user.email_user('Sicherheitshinweis: EMail-Änderung' + ts, t.render(context={
                    'Firstname': user.first_name, 'random': str(random.randint(0, 9)),'from_email':user.email,'to_email':email}))
                # change it
                serializer = UserSerializer(user, data=data, partial=True)
                if serializer.is_valid():
                    with transaction.atomic():
                        serializer.save(is_emailvalidated=False)
                        return Response(data={'new_email': email}, status=status.HTTP_200_OK)
                else:
                    return Response({'error': 'User not found or Emailtoken not valid'},
                                    status=status.HTTP_400_BAD_REQUEST)
            except:
                return Response({'error': 'User not found or Emailtoken not valid'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'No email or emailtoken provided'}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True)
    @permission_classes([IsAuthenticated])
    def validate_email(self, request, pk=None):
        """
        Set email validated after checking token
        :param
        :return: user object
        """
        data = request.data
        emailtoken = data['emailtoken']
        if len(emailtoken) > 0:
            try:
                user = User.objects.get(username=pk, emailtoken=emailtoken)
                # check if email already exists
                user.is_emailvalidated=True
                user.save()
                serializer = UserSerializer(user)
                return Response(serializer.data, status=status.HTTP_200_OK)
            except:
                return Response({'error': 'User not found or Emailtoken not valid'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'No email or emailtoken provided'}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    @permission_classes([IsAuthenticated])
    def email_user(self, request, pk=None):
        user = User.objects.get(username=pk)
        emailtype = request.GET.get('type', None)
        emailfolder = request.GET.get('folder', 'user')
        add_info = request.GET.get('add_info', '')
        if "{" in add_info: # if this is json string parse it
            add_info=json.loads(add_info)
        if emailtype:
            try:
                t = get_template(emailfolder + '/' + emailtype + '.html')
                ts = str(datetime.now(timezone.utc))
                user.email_user('DienZtliche Mail: ' + emailtype + '(' + ts + ')', t.render(context={
                    'Firstname': user.first_name, 'random': str(random.randint(0, 9)), 'add_info': add_info}))
                return Response({'username': user.username}, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response(data={"error": "Email Type not specified"}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    @permission_classes([IsAuthenticated])
    def email_user_token(self, request, pk=None):
        """
        Send 6-char token via email to user upon request
        :param
        :return: username
        """
        try:
            user = User.objects.get(username=pk)
            serializer = UserSerializer(user, data=request.data,
                                        partial=True)  # set partial=True to update a data partially
            if serializer.is_valid():
                with transaction.atomic():
                    token = user.create_email_token()
                    serializer.save(emailtoken=token, is_emailvalidated=False)
                    t = get_template('user/email-token.html')
                    ts = str(datetime.now(timezone.utc))
                    user.email_user('EMail Token' + ts, t.render(context={
                        'Firstname': user.first_name,
                        'token': token}))
                    return Response({'username': user.username}, status=status.HTTP_200_OK)
            else:
                Response({'error': 'User not found'}, status=status.HTTP_400_BAD_REQUEST)
        except:
            return Response({'error': 'User not found'}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True)
    @permission_classes([IsAuthenticated])
    def email_institution(self, request, pk=None):
        """
        Bulk email to the users of my institution
        :param request:
        :param pk:
        :return:
        """
        user = User.objects.get(username=pk)
        emailtype = request.data.get('type', None)
        emailfolder = request.data.get('folder', 'user')
        addressee = request.data.get('to_group', None)
        add_info = request.data.get('add_info', None)
        if emailtype:
            try:
                t = get_template(emailfolder + '/' + emailtype + '.html')
                ts = str(datetime.now(timezone.utc))
                if addressee == 'physicians':
                    user.email_physicians_of_my_institution('DienZtliche Mail: ' + emailtype + '(' + ts + ')',
                                                            t.render(context={
                                                                'add_info': add_info,
                                                                'random': str(random.randint(0, 9))}))
                else:
                    user.email_everyone_of_my_institution('DienZtliche Mail: ' + emailtype + '(' + ts + ')',
                                                          t.render(context={
                                                              'add_info': add_info,
                                                              'random': str(random.randint(0, 9))}))
                return Response({'username': user.username}, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response(data={"error": "Email Type not specified"}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True)
    def setgroup(self, request, pk=None):
        try:
            user = User.objects.get(username=pk)
            g_name = request.data.get('group')
            g_replace = request.data.get('replace')
            g = Group.objects.get(name=g_name)
            if g_replace:
                user.groups.clear()
            user.groups.add(g)
            ga = user.groups.all()
            return Response(data={"groups": [x.name for x in ga]}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False)
    @permission_classes([IsVerwaltung])
    def getmitarbeiterohnezuordnung(self, request):
        """
        Mitarbeiter ohne Institutszuordnung finden
        :param request:
        :return:
        """
        try:
            queryset = User.objects.filter(institution_link__isnull=True, is_staff=False)
            serializer = MinimalUserSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False)
    @permission_classes([IsVerwaltung | IsPlaner])
    def getmitarbeiterofmyinstitution(self, request):
        """
        Mitarbeiter meines Instituts zurückgeben
        :param request: subset = either 'administration' => ADMINISTRATION_GROUPS members, 'physician' => all - administration, None => all
        :return:
        """
        try:
            subset = request.GET.get('subset', None)
            myinstitution = request.user.institution_link
            queryset = User.objects.filter(institution_link=myinstitution)
            if subset is not None:
                if subset == 'administration':
                    queryset = User.objects.filter(institution_link=myinstitution,
                                                   groups__name__in=ADMINISTRATION_GROUPS).distinct().order_by(
                        'last_name')
                if subset == 'physician':
                    # get the other groups of this institution
                    all_groups = list(set(sum([[g.name for g in u.groups.all()] for u in queryset], [])))
                    physician_groups = [x for x in all_groups if x not in (ADMINISTRATION_GROUPS + BASIC_GROUPS)]
                    queryset = User.objects.filter(institution_link=myinstitution,
                                                   groups__name__in=physician_groups).distinct().order_by('last_name')
            serializer = MinimalUserSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False)
    @permission_classes([IsVerwaltung | IsPlaner])
    def updatemitarbeiterofmyinstitution(self, request, pk=None):
        """
        Fügt Mitarbeiter zur eigenen Institution hinzu und gibt die Liste aller zugehörigen Mitarbeiter zurück
        :param request:
        :param data: Array hinzuzufügender Mitarbeiter
        :return: Liste meiner Mitarbeiter
        """
        try:
            myinstitution = request.user.institution_link
            employees = myinstitution.employee.all()
            for e in employees:  # all employees are unlinked from the current institution
                if e != request.user:  # just to make sure not to throw out everybody
                    e.institution_link = None
                    e.save()
            userarray = request.data['data']
            for user in userarray:  # now set those users to belong to the institution
                user = User.objects.get(username=user)
                user.institution_link = myinstitution
                user.save()
            queryset = User.objects.filter(institution_link=myinstitution)
            serializer = MinimalUserSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False)
    @permission_classes([IsVerwaltung | IsPlaner])
    def addmitarbeitertomyinstitution(self, request, pk=None):
        """
        Fügt Mitarbeiter zur eigenen Institution hinzu und gibt die Liste aller zugehörigen Mitarbeiter zurück
        :param request:
        :param data: Array hinzuzufügender Mitarbeiter
        :return: Liste meiner Mitarbeiter
        """
        try:
            myinstitution = request.user.institution_link
            userarray = request.data
            for user in userarray:
                user = User.objects.get(username=user)
                user.institution_link = myinstitution
                user.save()
            queryset = User.objects.filter(institution_link=myinstitution)
            serializer = MinimalUserSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False)
    @permission_classes([IsVerwaltung | IsPlaner])
    def removemitarbeiterfrommyinstitution(self, request, pk=None):
        """
        Löscht Mitarbeiter aus der eigenen Mitarbeiterliste und gibt die Liste aller zugehörigen Mitarbeiter zurück
        :param request:
        :param data: Array zu löschender Mitarbeiter
        :return: Liste meiner Mitarbeiter
        """
        try:
            myinstitution = request.user.institution_link
            userarray = request.data
            for user in userarray:
                user = User.objects.get(username=user)
                user.institution_link = None
                user.save()
            queryset = User.objects.filter(institution_link=myinstitution)
            serializer = MinimalUserSerializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False)
    @permission_classes([IsVerwaltung | IsPlaner])
    def bulkupdategroups(self, request):
        """
        Bulk update for eigene Mitarbeiter
        :param request:
        :param data: Array of Records
        :return:
        """
        try:
            userarray = request.data['data']
            for u in userarray:
                user = User.objects.get(username=u['username'])
                user.groups.clear()
                groups = u['groups']
                for gname in groups:
                    g, created = Group.objects.get_or_create(name=gname)
                    user.groups.add(g)
                    # Set the permissions for the new user group from the Arzt group
                    if created:
                        g_arzt = Group.objects.get(name='Arzt')
                        p_codes = [p.id for p in g_arzt.permissions.all()]
                        g.permissions.set(p_codes)
                user.save()
            return Response(data={'message': 'ok'}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    @permission_classes([IsAuthenticated])
    def getabrechnungsmatrix(self, request, pk=None):
        try:
            u = User.objects.get(username=pk)
            yearMonth = request.GET.get('subset', None)
            try:
                if yearMonth:
                    # try to find Abrechnung objects
                    abr = Abrechnung.objects.filter(yearmonth=yearMonth, owner=u, institution_link=u.institution_link)
                    if len(abr) > 0:
                        serializer = AbrechnungSerializer(abr.last())
                        return Response(serializer.data, status=status.HTTP_200_OK)
                        # stundenmatrix = abr.last().data_stunden_encrypted
                        # if stundenmatrix:
                        #    return HttpResponse(json.dumps(stundenmatrix), content_type='application/json')
                    # if non found retrieve the prefabricated
                    df = u.retrieveAbrechnungsMatrix()
                    df = df.loc[yearMonth]
                    dj = df.to_json(force_ascii=False, date_format='iso', date_unit='s', orient='index')
                    return HttpResponse(dj, content_type='application/json')
                else:
                    return HttpResponse([], content_type='application/json')
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    @permission_classes([IsAuthenticated])
    def deleteabrechnungsmatrix(self, request, pk=None):
        try:
            u = User.objects.get(username=pk)
            yearMonth = request.GET.get('subset', None)
            try:
                if yearMonth:
                    # try to find Abrechnung objects
                    abr = Abrechnung.objects.filter(yearmonth=yearMonth, owner=u, institution_link=u.institution_link)
                    if len(abr) > 0:
                        abr.delete()
                    # now return the prefabricated
                    df = u.retrieveAbrechnungsMatrix()
                    df = df.loc[yearMonth]
                    dj = df.to_json(force_ascii=False, date_format='iso', date_unit='s', orient='index')
                    return HttpResponse(dj, content_type='application/json')
                else:
                    return HttpResponse([], content_type='application/json')
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True)
    @permission_classes([IsAuthenticated])
    def deleteuseraccount(self, request, pk=None):
        data = request.data
        emailtoken = data['emailtoken']
        if len(emailtoken) > 0:
            try:
                user = User.objects.get(username=pk, emailtoken=emailtoken)
                # check if email already exists
                planer = user.get_planer_groups_of_my_institution()
                t = get_template('user/email-for-planer-user-left.html')
                ts = str(datetime.now(timezone.utc))
                for p in planer:
                    p.email_user('Account wurde gelöscht' + ts, t.render(context={
                        'Firstname': p.first_name,
                        'user_firstname': user.first_name,
                        'user_lastname': user.last_name}))
                user.delete()
                return Response(data={'user deleted': 'Ok'}, status=status.HTTP_200_OK)
            except:
                return Response({'error': 'User not found or Emailtoken not valid'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'No email or emailtoken provided'}, status=status.HTTP_400_BAD_REQUEST)


class UserRegister(OAuthLibMixin, APIView):  ##CsrfExemptMixin,
    """
    Registering Users at OAuth Server
    """
    permission_classes = (AllowAny,)
    server_class = oauth2_settings.OAUTH2_SERVER_CLASS
    validator_class = oauth2_settings.OAUTH2_VALIDATOR_CLASS
    oauthlib_backend_class = oauth2_settings.OAUTH2_BACKEND_CLASS

    def post(self, request):
        data = request.data
        data = data.dict()
        serializer = RegisterSerializer(data=data)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    user = serializer.save()
                    if not ('username' in request.data.keys()):
                        request.POST._mutable = True
                        request.POST['username'] = user.username
                    if 'codename' in request.data.keys():
                        cn = request.POST['codename']
                        if len(cn) > 3:
                            institution = Institution.objects.filter(codename=cn)
                            if len(institution) > 0:
                                i = institution.first()
                                user.institution_link = i  # den Benutzer der gefundendenen Institution zubuchen
                                user.groups.add(
                                    Group.objects.get(name__exact='Reader'))  # Leserechte für Dienstplan vergeben
                                user.groups.remove(Group.objects.get(name__exact='Basic'))
                                user.save()
                    if 'notify' in request.data.keys():
                        notify = request.POST['notify']
                        if notify == 'new clinic':
                            u_staff = User.objects.filter(is_staff=True)
                            for u in u_staff:
                                # email notification to staff
                                u.email_user('Neue Klinik', '<a href=mailto:%s>%s</a>' % (
                                    user.email, (user.first_name + ' ' + user.last_name)))
                            # EMail an neuen Nutzer
                            t = get_template('user/newclinic.html')
                            user.email_user('DienZtliche Mail: Neueinrichtung Klinik', t.render(context={
                                'Firstname': user.first_name, 'random': str(random.randint(0, 9))}))
                            # set groups
                            user.groups.add(Group.objects.get(name__exact='Verwaltung'))
                            user.groups.add(Group.objects.get(name__exact='Planer'))
                            user.groups.remove(Group.objects.get(name__exact='Basic'))
                            user.save()
                    url, headers, body, token_status = self.create_token_response(request)
                    jbody = json.loads(body)
                    if token_status != 200:
                        raise Exception(jbody.get("error", ""))
                    return Response({**jbody, 'username': user.username}, status=token_status)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@csrf_exempt
@api_view(['GET'])
def check_token(request):
    """
    Retrieves User Profile from registered token
    :param Bearer token in Header is sufficient
    :param format:
    :return: User object
    """
    app_tk = request.META["HTTP_AUTHORIZATION"]
    m = re.search('(Bearer)(\s)(.*)', app_tk)

    app_tk = m.group(3)
    try:
        # search oauth2 token db to find user
        acc_tk = AccessToken.objects.get(token=app_tk)
        if hasExpired(acc_tk.expires):
            return Response({'error': 'Token has expired. Please log in again.'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'error': 'User not found.' + str(e)}, status=status.HTTP_400_BAD_REQUEST)
    user = acc_tk.user

    # in case of mfa requirement, check mfa token
    mfa_token = request.GET.get('mfa_token',None)
    if mfa_token is not None:
        r = user.validate_mfa_token(mfa_token)
        if (r.status_code == 200):
            pass
        else:
            return Response({'error': 'MFA invalid'}, status=status.HTTP_400_BAD_REQUEST)

    lastlogin = datetime.now(timezone.utc)
    user.last_login = lastlogin
    # create pi passkey for 2fa
    user.pi_passkey, _ = user.create_pi_passkey(lastlogin)
    if user.pi_serialnumber == '':
        user.pi_status='NO'
    user.pi_accesstoken={}
    user.save()
    serializer = UserSerializer(user)
    return Response(serializer.data, status=status.HTTP_200_OK)


class TokenView(TV):
    """
    Get OAuth Access Token from Login with email or refreshToken or username
    """

    def create_token_response(self, request):
        request.POST._mutable = True
        email = request.POST.pop('email', None)
        username = request.POST.pop('username', None)
        print('Requesting user: %s' % username)
        refresh_token = request.POST.pop('refresh_token', None)
        if type(refresh_token) == list:
            refresh_token = refresh_token[0]
        if username:
            user = get_user_model().objects.get(username=username[0])
            request.POST['username'] = username[0]
        if email:
            username = get_user_model().objects.filter(email__iexact=email[0]).values_list('username',
                                                                                           flat=True).last()  # iexact = case insensitive
            request.POST['username'] = username
        if refresh_token:
            try:
                rt = RefreshToken.objects.get(token__exact=refresh_token)
                user = rt.user
                rt.revoke()
                request.POST['username'] = user.username
                client_id = request.POST.get('client_id')
                application = Application.objects.get(client_id=client_id)
                scope = request.POST.get('scope')
                present = datetime.now().replace(tzinfo=timezone.utc)
                expires = present + timedelta(seconds=oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS)
                acc_token = AccessToken.objects.create(user=user, application=application, expires=expires,
                                                       token=common.generate_token(), scope=scope)
                r_token = RefreshToken.objects.create(user=user, application=application, access_token=acc_token,
                                                      token=common.generate_token())
                request.POST['refresh_token'] = str(r_token)
                # rt.access_token=acc_token
                # rt.save()
                # acc_token.save()
            except Exception as e:
                print(e)
        return super(TokenView, self).create_token_response(request)


class ChangePasswordView(UpdateAPIView):
    """
        An endpoint for changing password.
        """
    serializer_class = ChangePasswordSerializer
    model = User
    permission_classes = (IsAuthenticated,)

    def get_object(self, queryset=None):
        obj = self.request.user
        return obj

    def update(self, request, *args, **kwargs):
        self.object = self.get_object()
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            # Check old password
            if not self.object.check_password(serializer.data.get("old_password")):
                return Response({"old_password": ["Wrong password."]}, status=status.HTTP_400_BAD_REQUEST)
            # set_password also hashes the password that the user will get
            self.object.set_password(serializer.data.get("new_password"))
            self.object.save()
            response = {
                'status': 'success',
                'code': status.HTTP_200_OK,
                'message': 'Password updated successfully',
                'data': []
            }

            return Response(response)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@csrf_exempt
@api_view(['GET'])
def forgot_password(request):
    """
    Send 6-char token via email to user upon request
    :param
    :return: username
    """
    try:
        email = request.GET.get('email')
        user = get_user_model().objects.get(email__iexact=email)
        serializer = UserSerializer(user, data=request.data,
                                    partial=True)  # set partial=True to update a data partially
        if serializer.is_valid():
            with transaction.atomic():
                token = user.create_email_token()
                serializer.save(emailtoken=token, is_emailvalidated=False)
                t = get_template('user/email-forgotpassword.html')
                ts = str(datetime.now(timezone.utc))
                user.email_user('Passwort vergessen. ' + ts, t.render(context={
                    'Firstname': user.first_name,
                    'token': token}))
                return Response({'username': user.username}, status=status.HTTP_200_OK)
        else:
            Response({'error': 'User not found'}, status=status.HTTP_400_BAD_REQUEST)
    except:
        return Response({'error': 'User not found'}, status=status.HTTP_400_BAD_REQUEST)


@csrf_exempt
@api_view(['POST'])
def set_new_password(request):
    """
    Set new Password and Refreshes Expiry
    :param
    :return: username
    """
    data = request.data
    username = data['username']
    emailtoken = data['emailtoken']
    if ((len(username) > 0) & (len(emailtoken) > 0)):
        try:
            user = User.objects.get(username=username, emailtoken=emailtoken)
            serializer = ForgotPasswordSerializer(user, data=data, partial=True)
            if serializer.is_valid():
                with transaction.atomic():
                    serializer.save()
                    return Response(data={'new_password': 'OK'}, status=status.HTTP_200_OK)
            else:
                return Response({'error': 'User not found or Emailtoken not valid'}, status=status.HTTP_400_BAD_REQUEST)
        except:
            return Response({'error': 'User not found or Emailtoken not valid'}, status=status.HTTP_400_BAD_REQUEST)
    else:
        return Response({'error': 'No Username or emailtoken provided'}, status=status.HTTP_400_BAD_REQUEST)


class InstitutionViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing institution instances.
    """
    queryset = Institution.objects.all()
    authentication_classes = [OAuth2Authentication]
    serializer_class = InstitutionSerializer

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action == 'list':
            permission_classes = [IsAdminUser]
        elif self.action == 'getsocialscores':
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [IsAuthenticated, IsMyInstitution, IsVerwaltung | IsPlaner]
        return [permission() for permission in permission_classes]

    def create(self, request, *args, **kwargs):
        data = request.data
        user = request.user
        serializer = InstitutionSerializer(data=data)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    inst = serializer.save()
                    user.institution_link = inst
                    user.save()
                    return Response(serializer.data, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs):
        inst = Institution.objects.get(id=pk)
        user = request.user
        serializer = self.get_serializer(inst, data=request.data, partial=True)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    user.institution_link = inst
                    user.save()
                    serializer.save()
                    return Response(serializer.data, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    def checkcodename(self, request, pk=None):
        """
        checkt, ob der gesendete codename zur Institution bereits gehört, oder ob er verfügbar (=unique) ist
        :param request: codename
        :param pk: institution
        :return: 200, wenn o.a. true, ansonsten 400
        """
        try:
            cn = request.GET.get('codename', None)
            inst = Institution.objects.get(id=pk)
            cn_belongs_to_this_institution = inst.codename == cn
            if cn_belongs_to_this_institution:
                return Response(status=status.HTTP_200_OK)
            else:
                i = Institution.objects.filter(codename=cn)
                if len(i) == 0:
                    return Response(status=status.HTTP_200_OK)
                else:
                    return Response(status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    def getsocialscores(self, request, pk=None):
        """
        Ermittelt die Social Scores der Arzt Wishes
        :param request:
        :param pk: institution
        :return: {'user_scores': {username: score}, 'mean_score':mean_score,'stddev':std_score}
        """
        if pk is not None:
            try:
                ym = request.GET.get('yearmonth', None)
                inst = Institution.objects.get(id=pk)
                res = inst.getPhysiciansSocialScores(ym=ym)
                return Response(data=res, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response(data={"error": 'No institution specified'}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    def automakedienstplan(self, request, pk=None):
        """
        Erstellt automatisch einen Dienstplan für den gewünschten Monat
        :param request: yearmonth YYYY-MM
        :param pk: Institution id
        :return:
        """
        if pk is not None:
            try:
                ym = request.GET.get('yearmonth', None)
                automake_dienstplan_for_institution(pk, ym)
                return Response(data={'message':'Dienstplan created'}, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response(data={"error": 'No institution specified'}, status=status.HTTP_400_BAD_REQUEST)


class AbrechnungsViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing Abrechnung instances.
    """
    queryset = Abrechnung.objects.all()
    authentication_classes = [OAuth2Authentication]
    serializer_class = AbrechnungSerializer

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action == 'list':
            permission_classes = [IsAdminUser | IsOwner]
        else:
            permission_classes = [IsAuthenticated, IsOwner]
        return [permission() for permission in permission_classes]

    def create(self, request, *args, **kwargs):
        data = request.data
        ym = data['yearmonth']
        if not ym:
            return Response(data={'error': 'no yearmonth timepoint supplied'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            abrechnung_instance = Abrechnung.objects.filter(owner=request.user, yearmonth=ym)
            if len(abrechnung_instance) > 0:
                serializer = AbrechnungSerializer(instance=abrechnung_instance.last(), data=data)
            else:
                serializer = AbrechnungSerializer(data=data)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if serializer.is_valid():
            try:
                with transaction.atomic():
                    serializer.save(owner=request.user, institution_link=request.user.institution_link,
                                    status='Erstellt')
                    return Response(serializer.data, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DienstModellViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing institution instances.
    """
    queryset = DienstModell.objects.all()
    authentication_classes = [OAuth2Authentication]
    serializer_class = DienstModellSerializer

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action == 'list':
            permission_classes = [IsAdminUser]
        else:
            permission_classes = [IsAuthenticated, IsMyInstitutionsDienstModell, IsVerwaltung]
        return [permission() for permission in permission_classes]
