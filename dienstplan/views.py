import json
import random
from datetime import datetime, timedelta, timezone
from io import BytesIO

from django.contrib.auth.models import Group
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import get_template
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from rest_framework import viewsets, status
from rest_framework.decorators import action, permission_classes, renderer_classes, api_view
from rest_framework.permissions import DjangoModelPermissions, IsAuthenticated, IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from DienstplanerAPI.settings import FRONTEND_URL
from dienstplan.models import WishData, DienstplanData, DienstTrade
from dienstplan.serializers import WishSerializer, DienstplanSerializer, DienstTradeSerializer
from dienstplan.utils import getLastWishes, generateDienstplanFromWishlist, automake_dienstplan_for_institution, \
    automake_dienstplan_for_anonymous
from users.models import User
from users.permissions import IsPlaner

import pandas as pd


# Create your views here.



class WishView(viewsets.ModelViewSet):
    serializer_class = WishSerializer
    authentication_classes = [OAuth2Authentication]
    queryset = WishData.objects.all()

    def get_queryset(self):
        if self.action == 'list':
            user = self.request.user
            showall= user.groups.filter(name='Verwaltung').exists() | user.groups.filter(name='Planer').exists()
            if showall:
                return self.queryset.filter(owner__in=self.request.user.institution_link.employee.all()) # only the wishes from the same institution shall be displayed
            else:
                return self.queryset.filter(owner=user)
        else:
            return self.queryset

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        permission_classes = [IsAuthenticated]
        if self.action == 'list':
            permission_classes+= [IsAdminUser]
        return [permission() for permission in permission_classes]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(methods=['get'], detail=False)
    def getlast(self, request):
        """
        Get Last Wish
        :param request: referring date in YYYY-MM format
        :return: last Wish of the requesting user
        """
        try:
            referring_date_ym = request.GET.get('referring_yearmonth', None)
            if referring_date_ym:
                # fast search
                wishquery = WishData.objects.filter(yearmonth=referring_date_ym, owner__exact=request.user)
                if len(wishquery)>0:
                    wish=wishquery.latest('created')
                else:
                    return Response(data={}, status=status.HTTP_200_OK)
            else:
                wish = WishData.objects.filter(owner__exact=request.user).latest('created')
            serializer = WishSerializer(wish, many=False)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    def getsocialscore(self, request, pk=None):
        """
        Returns the social score of the requested wish
        :param request: pk = id of Wish
        :return: Social Score
        """
        try:
            if pk is not None:
                wish = WishData.objects.get(id=pk)
                score = wish.getSocialScore()
                return Response(data={'score':score}, status=status.HTTP_200_OK)
            else:
                return Response(data={'score':0}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False)
    def getlastwishes(self, request):
        """
        Get Last Wishes from the users of the same institution as the requesting user
        Wishes shall be limited to the referring Date as passed
        :param referring = referring date of to check for wishes of the same month, iso format
                group should be the kind of Arzt
        :return:
        """
        try:
            groupname = request.GET.get('group','Arzt')
            referring_date=request.GET.get('referring',None)
            institution = self.request.user.institution_link
            lastwishes, nonwishers, participating_users =getLastWishes(groupname,referring_date,institution)

            if len(lastwishes)==0:
                return Response(data={}, status=status.HTTP_200_OK)
            df = pd.concat(lastwishes, axis=1, keys=[s.name for s in lastwishes]).fillna('maybe')
            dj = df.to_json(force_ascii=False, date_format='iso', date_unit='s', orient='index')
            di = {"users":[{'id': u.username, 'first_name':u.first_name, 'last_name': u.last_name} for u in participating_users],"matrix":json.loads(dj),"nonwishers":nonwishers}
            #serializer = WishSerializer(df)
            return HttpResponse(json.dumps(di, ensure_ascii=False), content_type='application/json')
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


    @action(methods=['post'], detail=False)
    def automakedienstplan(self, request):
        data = request.data
        df_wishes=None
        df_bootstrap = None
        wish_encoder = {}
        physician_preferences = None
        try:
            if 'wishes' in data.keys():
                wish_dict=dict(data['wishes'])
                df_wishes = pd.DataFrame.from_dict(wish_dict['matrix'], orient='index')
                df_wishes.index=pd.to_datetime(df_wishes.index)
            if 'bootstrap' in data.keys():
                if data['bootstrap'] is not None:
                    boot_dict = dict(data['bootstrap'])
                    df_bootstrap = pd.Series(boot_dict)
                    df_bootstrap.index = pd.to_datetime(df_bootstrap.index,format='%d.%m.%Y')
            if 'wishCalculationParams' in data.keys():
                wish_encoder = dict(data['wishCalculationParams'])
                wish_encoder = {k: int(v) for (k,v) in wish_encoder.items()} # convert to int
            if 'physician_preferences' in data.keys():
                physician_preferences = dict(data['physician_preferences'])
            if df_wishes is not None:
                result={}
                dpls = generateDienstplanFromWishlist(df_wishes,df_bootstrap,distribution_inequality=int(data['distribution_inequality']),wish_encodings=wish_encoder,physicianMinMaxPreferences=physician_preferences)
                if len(dpls)>0:
                    # sort the results so that the dienstplan with the most spread of Diensts (as median to allow for single doctors to have narrow diensts) is returned
                    dpls.sort(key=lambda x: x['median'], reverse=True)
                    result = dpls[0]['roster']
                return HttpResponse(json.dumps(result), content_type='application/json')
            else:
                return Response(data={"error": "no wishes given"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class DienstplanView(viewsets.ModelViewSet):
    serializer_class = DienstplanSerializer
    authentication_classes = [OAuth2Authentication]
    queryset = DienstplanData.objects.all()

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        permission_classes = [IsAuthenticated]
        if self.action == 'create':
            permission_classes += [IsPlaner]
        return [permission() for permission in permission_classes]

    def list(self, request, *args, **kwargs):
        u = self.request.user
        showall = request.GET.get('all', None)
        if (showall=='yes') | (showall=='true'):
            d = self.queryset.filter(institution_link__exact=u.institution_link)
            queryset = d.order_by('-referringDate','-created')
        elif (showall=='all-latest'):
            d = self.queryset.filter(institution_link__exact=u.institution_link)
            # get the referringDates, make them unique with set
            rdates = list(set([r.referringDate for r in d.all()]))
            ids = []
            for rdate in rdates:
                dpl = d.filter(referringDate=rdate).order_by('-created')[0]
                ids.append(dpl.id)
            queryset = d.filter(id__in=ids).order_by('-referringDate')
        elif (showall=='latest-6mths'):
            tp = datetime.now() - timedelta(days=180)
            d = self.queryset.filter(institution_link__exact=u.institution_link, referringDate__gt=tp, status__in=['Geplant','Skizze'])
            # get the referringDates, make them unique with set
            rdates = list(set([r.referringDate for r in d.all()]))
            ids = []
            for rdate in rdates:
                dpl = d.filter(referringDate=rdate).order_by('-created')[0]
                ids.append(dpl.id)
            queryset = d.filter(id__in=ids).order_by('-referringDate')
        else:
            d = self.queryset.filter(institution_link__exact=u.institution_link, status__in=['Geplant'])
            # get the referringDates, make them unique with set
            rdates = list(set([r.referringDate for r in d.all()]))
            ids = []
            for rdate in rdates:
                dpl = d.filter(referringDate=rdate).order_by('-created')[0]
                ids.append(dpl.id)
            queryset = d.filter(id__in=ids).order_by('-referringDate')
        serializer = DienstplanSerializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        data = request.data
        user = request.user
        if data['status'] == 'Angelegt':
            try:
                refDateStr = data['referringDate']
                refDate = datetime.fromisoformat(refDateStr)
                q = DienstplanData.objects.filter(status='Angelegt',referringDate__month=str(refDate.month), referringDate__year=str(refDate.year), institution_link=user.institution_link)
                if len(q)>0: # just in case there is already a dienstplan for this institution created
                    serializer=DienstplanSerializer(q.first())
                    return Response(serializer.data, status=status.HTTP_200_OK)
            except:
                pass
        serializer = DienstplanSerializer(data=data)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    dpl = serializer.save()
                    dpl.institution_link=user.institution_link
                    dpl.creator=user
                    dpl.autocreateFeiertage()
                    dpl.save()
                    return Response(serializer.data, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs):
        # if another guy modifies the Dienstplan, set him as cocreator
        dpl=DienstplanData.objects.get(id=pk)
        user = request.user
        serializer = self.get_serializer(dpl, data=request.data, partial=True)
        if serializer.is_valid():
            try:
                with transaction.atomic():
                    if user != dpl.creator:
                        dpl.cocreators.add(user)
                    dpl.save()
                    serializer.save()
                    return Response(serializer.data, status=status.HTTP_200_OK)
            except Exception as e:
                return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False)
    def getalreadyangelegtedienstplanreferringdates(self, request):
        try:
            inst = request.user.institution_link
            dpl = DienstplanData.objects.filter(status__exact='Angelegt', institution_link__exact=inst).all()
            res = [d.referringDate for d in dpl]
            return Response(data=res, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=False)
    def changefeiertagebyreferringdate(self,request):
        try:
            refDateStr = request.data['referringDate']
            feiertage = request.data['Feiertage']
            inst = request.user.institution_link
            dpl = DienstplanData.objects.filter(institution_link__exact=inst, referringDate=refDateStr).all()
            for d in dpl:
                d.data={**d.data,'Feiertage':feiertage}
                d.save()
            return Response(status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False)
    def getcurrentdienstplantask(self, request):
        try:
            inst = request.user.institution_link
            # retrieve those Angelegt objects, where no Geplant object exists, Skizze maybe
            dpl_angelegt_refDates = DienstplanData.objects.filter(status__exact='Angelegt',institution_link__exact=inst).values('referringDate') #.latest('created')
            dpl_geplant_refDates = DienstplanData.objects.filter(status__exact='Geplant',institution_link__exact=inst).values('referringDate')
            dpl_refDates_diff = [d['referringDate'] for d in dpl_angelegt_refDates if d not in dpl_geplant_refDates]
            dpl=DienstplanData.objects.filter(status__exact='Angelegt',institution_link__exact=inst, referringDate__in=dpl_refDates_diff)
            serializer = DienstplanSerializer(dpl, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=True)
    def getdienstplanxls(self, request, pk=None):
        try:
            inst = request.user.institution_link
            dpl = DienstplanData.objects.get(id=pk, institution_link__exact=inst)
            if dpl is not None:
                df=dpl.to_dataframe_for_xls()
                with BytesIO() as b:
                    with pd.ExcelWriter(b) as writer:
                        df.to_excel(writer, sheet_name="Dienstplan", index=True)
                    response = HttpResponse(b.getvalue(),
                                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    response['Content-Disposition'] = 'attachment; filename=dienstplan.xlsx'
                    return response
            else:
                return Response(data={"error": 'No Dienstplan object found'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['get'], detail=False)
    def autocreate(self, request):
        institution_id = request.institution_link.id
        yearmonth = request.GET.get('yearmonth', None)
        dpl = automake_dienstplan_for_institution(institution_id, yearmonth)
        serializer = DienstplanSerializer(dpl)
        return Response(data=serializer.data, status=status.HTTP_200_OK)


class DienstTradeView(viewsets.ModelViewSet):
    serializer_class = DienstTradeSerializer
    authentication_classes = [OAuth2Authentication]
    queryset = DienstTrade.objects.all()

    def get_queryset(self):
        if self.action == 'list':
            return self.queryset.filter(institution_link=self.request.user.institution_link).distinct()
        else:
            return self.queryset

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        permission_classes = [IsAuthenticated]
        return [permission() for permission in permission_classes]
    def create(self, request, *args, **kwargs):
        try:
            data = request.data
            referringDate = data.get('referringDate',None)
            referringDienstgroup = data.get('referringDienstgroup',None)
            trade = DienstTrade.objects.filter(institution_link=request.user.institution_link, referringDienstgroup=referringDienstgroup, referringDate=referringDate)
            if (len(trade) > 0):
                serializer = self.get_serializer(trade[0], data=data, partial=True)
                if serializer.is_valid():
                    serializer.save()
                    trade[0].calculateStatus()
                    serializer = self.get_serializer(trade[0])
                    trade[0].notifyAboutChanges(request_user=request.user)
                    return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                serializer = self.get_serializer(data=data)
                if serializer.is_valid():
                    serializer.save(institution_link=request.user.institution_link, creator=request.user)
                    return Response(serializer.data, status=status.HTTP_200_OK)
                else:
                    return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, pk=None, *args, **kwargs):
        try:
            partial = kwargs.pop('partial', False)
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            instance.calculateStatus()
            serializer = self.get_serializer(instance)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs):
        try:
            trade=DienstTrade.objects.get(id=pk, institution_link=request.user.institution_link)
            trade.notifyAboutRemoval(request_user=request.user)
            connected_trades = trade.connected_trades.all()
            trade.delete()
            for t in connected_trades:
                t.calculateStatus()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(methods=['post'], detail=True)
    def addtotrade(self, request, pk=None):
        """
        adds a trade to another trade
        :param request: tradetoadd: id of the trade that should be linked
        :param pk: Hosttrade to which a trade is to be added
        :return: Hosttrade
        """
        try:
            if pk is not None:
                data = request.data
                trade_id_toadd=data.get('tradetoadd',None)
                hosttrade = DienstTrade.objects.get(id=pk)
                trade_to_add = DienstTrade.objects.get(id=trade_id_toadd)
                hosttrade.connected_trades.add(trade_to_add)
                hosttrade.save()
                hosttrade.calculateStatus()
                hosttrade.notifyAboutChanges(request_user=request.user)
                serializer = self.get_serializer(hosttrade)
                return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                return Response(data={"error": "Missing Hosttrade"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


    @action(methods=['post'], detail=True)
    def sendtraderequest(self, request, pk=None):
        """
        sends a trade request to another user
        :param request: receiver: username to whom the trade request is to be sent
        :param pk: trade
        :return:
        """
        try:
            if pk is not None:
                data = request.data
                receiver_id=data.get('receiver',None)
                group = data.get('group','')
                receiver = User.objects.get(username=receiver_id)
                trade = DienstTrade.objects.get(id=pk)
                emailtype='request'
                sender=request.user
                t = get_template('trader/' + emailtype + '.html')
                ts = str(datetime.now(timezone.utc))
                if trade.expose_user:
                    verb = 'übernehmen'
                else:
                    verb = 'abgeben'
                add_info={'sender': sender.first_name+' '+sender.last_name,
                          'group':group,
                          'referringDate':trade.referringDate,
                          'requested_action':verb,
                          'tradeurl': FRONTEND_URL+'dienstbroker?tradeid='+str(trade.id)}
                receiver.email_user('DienZt-Markt: ' + emailtype + '(' + ts + ')', t.render(context={
                    'Firstname': receiver.first_name, 'random': str(random.randint(0, 9)), 'add_info': add_info}))
                emails = trade.data.get('emails',[])
                emails.append({'receiver': receiver.username,'sender':sender.username, 'requested_action':verb, 'ts': ts})
                trade.data={**trade.data,'emails':emails}
                trade.save()
                serializer = self.get_serializer(trade)
                return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([AllowAny])
def dienstplanservice(request):
    try:
        data = request.data
        yearmonth=data.get('yearmonth',None)
        names = data.get('names',None)
        distribution_inequality = data.get('distribution_inequality',0)
        interval_minimizer = data.get('interval_minimizer',0)
        if (yearmonth is not None) & (names is not None):
            dpl=automake_dienstplan_for_anonymous(names, yearmonth, distribution_inequality, interval_minimizer)
            return Response(data=dpl, status=status.HTTP_200_OK)
        else:
            return Response(data={"error": "either names or yearmonth not provided"}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response(data={"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)