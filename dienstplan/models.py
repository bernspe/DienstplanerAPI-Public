import datetime
import random
import uuid

import requests
from django.db import models
import pandas as pd
from django.template.loader import get_template

from DienstplanerAPI import settings
from DienstplanerAPI.settings import FRONTEND_URL
from users.models import User

week_labels = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
DEFAULT_WD_SCORES = {'Mo':1,'Di':1,'Mi':1,'Do':1,'Fr':2,'Sa':3,'So':3}

# Create your models here.

def make_social_score_from_dates(wochentag, feiertag, wish='',dienst=0):
    # dienst = count of dienst at this day, usually at most 1
    wd_scores=DEFAULT_WD_SCORES
    # maybe there will be some need to dynamically modify the scores
    score=0
    score_me = False
    my_score = False
    factor = 1
    if (len(wish)>0) | (dienst>0):
        score_me = (wish=='yes') | (wish == 'maybe') | (wish == 'limited') | (dienst>0)
        if wish == 'limited':
            factor=0.5
        if (dienst>0):
            factor=dienst
        my_score = True
    if (len(wish)==0) & dienst==0:
        my_score=True
    if feiertag=='FT':
        if (my_score & ~score_me):
            return 0
        else:
            return wd_scores['So']*factor
    if feiertag=='VF':
        if (my_score & ~score_me):
            return 0
        else:
            return wd_scores['Sa']*factor
    if (my_score & ~score_me):
        return 0
    else:
        return wd_scores[wochentag]*factor


class WishData(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    yearmonth = models.CharField(max_length=8, null=True, blank=True)  # Bezug zu welchem Dienstmonat YYYY-MM
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='wishdata', on_delete=models.CASCADE, null=True,
                              blank=True)
    data = models.JSONField(null=True, blank=True)

    def to_series(self):
        idx = []
        d3 = []
        for d2 in self.data:
            idx.append(d2['date'])
            d3.append(d2['wish'])
        a = pd.Series(d3, index=idx, name=self.owner_id)
        i = pd.to_datetime(a.index, format=settings.DATEFORMAT_INPUT)
        a.index = i
        return a

    def getTimeConstraints(self):
        s = self.to_series()
        return s.index.min(), s.index.max()

    def getYearMonth(self):
        if self.yearmonth is None:
            min, max = self.getTimeConstraints()
            self.yearmonth = min.strftime('%Y-%m')
            self.save()
        return self.yearmonth

    def getSocialScore(self):
        wd = self.to_series()
        inst = self.owner.institution_link
        # Suche über YearMonth ist sicherer
        year = self.yearmonth.split('-')[0]
        month = self.yearmonth.split('-')[1]
        dpl = DienstplanData.objects.filter(status__exact='Angelegt', institution_link__exact=inst,referringDate__year=year, referringDate__month=month)
        if len(dpl)==0:
            return None
        scores = []
        for d in dpl:
            df=d.to_dataframe(how='username',other='lastname')
            df['wishes']=wd
            # assign maybe where whish is missing
            df['wishes']=df.wishes.fillna('maybe')
            df['social_score']=df.apply(lambda x: make_social_score_from_dates(x['Wochentag'],x['Feiertag'], dienst=-1), axis=1)
            max_social = df['social_score'].sum()
            df['my_social']=df.dropna(subset=['wishes']).apply(lambda x: make_social_score_from_dates(x['Wochentag'],x['Feiertag'],wish=x['wishes'], dienst=-1), axis=1)
            if max_social == 0:
                my_score=0
            else:
                my_score = df['my_social'].sum() / max_social * 100
            if self.owner.data_encrypted is not None:
                if 'Vertrags_Arbeitszeiten' in self.owner.data_encrypted.keys():
                    s=sum([float(i) for i in self.owner.data_encrypted['Vertrags_Arbeitszeiten'].values()])
                    if s==0:
                        s=40
                    my_score= my_score * 40 / s
            scores.append(my_score)
        return int(sum(scores) / len(scores))




class DienstplanData(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution_link = models.ForeignKey('users.Institution', related_name='dienstplan', on_delete=models.CASCADE,
                                         null=True, blank=True)
    creator = models.ForeignKey('users.User', related_name='dienstplan', on_delete=models.SET_NULL, null=True,
                                blank=True)
    cocreators = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='dienstplan_participate', blank=True)
    created = models.DateTimeField(auto_now_add=True)
    last_modified = models.DateTimeField(auto_now=True)
    referringDate = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, default="Angelegt")
    data = models.JSONField(null=True, blank=True)
    changelog = models.JSONField(default=list)


    def to_dataframe(self, how='lastname', other='username'):  # either by lastname or username
        data = self.data
        try:
            a = []
            for d in data['Dienstplaene']:
                if len(how)>0:
                    df_temp=pd.DataFrame.from_records(d['Dienstplan'], index='date').rename(columns={how: d['group']})
                    if other in df_temp.columns:
                        df_temp.drop(columns=[other],inplace=True)
                    a.append(df_temp)
                else:
                    a.append(pd.DataFrame.from_records(d['Dienstplan'], index='date'))
            df = pd.concat(a, axis=1)
            # fix missing Dienst dates
            dti = pd.to_datetime(df.index, format='%d.%m.%Y')
            start = dti.min()
            end = dti.max()
            fixed_index = pd.date_range(start=start, end=end, freq='D', normalize=True).strftime('%d.%m.%Y')
            df = df.reindex(fixed_index)
        except Exception as e:
            if self.referringDate is None:
                referring_date_timestamp = pd.Timestamp.now().astimezone(None)
            else:
                referring_date_timestamp = pd.Timestamp(self.referringDate).astimezone(None)
            min = pd.offsets.MonthBegin().rollback(referring_date_timestamp)
            max = referring_date_timestamp + pd.offsets.MonthEnd()
            dti = pd.date_range(start=min, end=max, freq='D', normalize=True)
            df = pd.DataFrame(index=dti)
        try:
            df.sort_index(inplace=True)
            df['Datum'] = df.index
            df.index = pd.to_datetime(df.index, format='%d.%m.%Y')
            df['Wochentag'] = df.index.day_of_week.map(lambda x: week_labels[x])
        except:
            pass
        try:
            ft = data['Feiertage']
            fti = pd.to_datetime(ft, dayfirst=True, format='mixed')
            vfi = (fti - pd.Timedelta('1 day'))
            df = df.assign(FT=lambda x: x.index.isin(fti), VF=lambda x: x.index.isin(vfi))
            df['Feiertag'] = df.apply(lambda x: 'FT' if x['FT'] else 'VF' if x['VF'] else '', axis=1)
            df.drop(columns=['FT', 'VF'], inplace=True)
        except:
            df['Feiertag'] = ''
        return df

    def to_dataframe_for_xls(self):
        try:
            df = self.to_dataframe(how='lastname',other='username')
            df.index = df['Datum']
            df.drop(columns=['Datum'], inplace=True)
            date_cols = ['Wochentag', 'Feiertag']
            df = df[['Wochentag'] + ['Feiertag'] + [col for col in df.columns if col not in date_cols]]  # reorder
        except:
            return
        return df

    def autocreateFeiertage(self):
        institution = self.institution_link
        referring_date= self.referringDate
        if referring_date is None:
            referring_date_timestamp = pd.Timestamp.now().astimezone(None)
        else:
            referring_date_timestamp = pd.Timestamp(referring_date).astimezone(None)
        min = pd.offsets.MonthBegin().rollback(referring_date_timestamp).strftime('%Y-%m-%d')
        max = (referring_date_timestamp + pd.offsets.MonthEnd()).strftime('%Y-%m-%d')
        if institution is not None and referring_date is not None:
            subdivision=institution.state_isocode
            if subdivision is not None and subdivision != '':
                try:
                    result = []
                    params={'countryIsoCode': 'DE', 'languageIsoCode':'DE','validFrom':min,'validTo':max}
                    r=requests.get('https://openholidaysapi.org/PublicHolidays',params=params)
                    if r.status_code == 200:
                        data=r.json()
                        for d in data:
                            if d['nationwide']:
                                result.append(datetime.datetime.strptime(d['endDate'], '%Y-%m-%d').strftime('%d/%m/%Y'))
                            else:
                                subs = list(map(lambda x: x['code'], d['subdivisions']))
                                if subdivision in subs:
                                    result.append(datetime.datetime.strptime(d['endDate'], '%Y-%m-%d').strftime('%d/%m/%Y'))
                        if self.data:
                            self.data={**self.data, **{'Feiertage': result}}
                        else:
                            self.data={'Feiertage': result}
                except Exception as e:
                    print('Feiertage could not be created: %s',str(e))
                    self.data={'Feiertage': result}





class DienstTrade(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institution_link = models.ForeignKey('users.Institution', related_name='diensttrade', on_delete=models.CASCADE,
                                         null=True, blank=True)
    expose_user = models.ForeignKey('users.User', related_name='trade_expose', on_delete=models.SET_NULL, null=True, blank=True)
    takeover_user = models.ForeignKey('users.User', related_name='trade_takeover', on_delete=models.SET_NULL, null=True,
                                    blank=True)
    requires_connected_trades = models.BooleanField(default=True)
    connected_trades = models.ManyToManyField('self', blank=True)
    status = models.CharField(max_length=20, default="open")
    creator = models.ForeignKey('users.User', related_name='trades', on_delete=models.SET_NULL, null=True,
                                blank=True)
    created = models.DateTimeField(auto_now_add=True)
    referringDate = models.DateField(blank=True, null=True)
    referringDienstgroup = models.CharField(max_length=20, default="Arzt")
    data = models.JSONField(default=dict)

    def calculateStatus(self):
        if not self.requires_connected_trades:
            if (self.expose_user is not None) & (self.takeover_user is not None):
                self.status = "settled"
            else:
                self.status = "open"
            self.save()
        else:
            result={}
            trade_list = [self,*self.connected_trades.all()]
            for c in trade_list:
                if c.expose_user is not None:
                    e_u = c.expose_user.username
                    if e_u not in result:
                        result[e_u]=[-1]
                    else:
                        result[e_u].append(-1)
                if c.takeover_user is not None:
                    t_u = c.takeover_user.username
                    if t_u not in result:
                        result[t_u]=[1]
                    else:
                        result[t_u].append(1)
            res_sum=-1
            if len(result.keys())>1: # there need to bee more than one trading partner
                res_sum = sum([sum(x) for x in result.values()])
            if res_sum == 0:
                for c in trade_list:
                    c.status = "settled"
                    c.save()
            else:
                for c in trade_list:
                    c.status = "open"
                    c.save()


    def notifyAboutChanges(self, request_user):
        """
        notifys about trade changes all users, even in connected trades but not the requesting user
        :param request_user:
        :return: the notified users
        """
        emailtype = 'notification'
        add_info = {'tradeurl': FRONTEND_URL + 'dienstbroker?tradeid=' + str(self.id)}
        t = get_template('trader/' + emailtype + '.html')
        ts = str(datetime.datetime.now(datetime.timezone.utc))
        notified_users=[]
        for u in list(set([self.expose_user, self.takeover_user, *[c.expose_user for c in self.connected_trades.all()], *[c.takeover_user for c in self.connected_trades.all()]])):
            if (u is not None) & (u is not request_user):
                u.email_user('DienZt-Markt: ' + emailtype + '(' + ts + ')', t.render(context={
                    'Firstname': u.first_name, 'random': str(random.randint(0, 9)), 'add_info': add_info}))
                notified_users.append(u)
        return notified_users

    def notifyAboutRemoval(self, request_user):
        """
        notifys about trade delete to all associated users, even in connected trades
        :param request_user:
        :return: the notified users
        """
        emailtype = 'notification-delete'
        add_info = {'tradeurl': FRONTEND_URL + 'dienstbroker', 'refDate':self.referringDate, 'deletingUser':request_user.first_name+' '+request_user.last_name }
        t = get_template('trader/' + emailtype + '.html')
        ts = str(datetime.datetime.now(datetime.timezone.utc))
        notified_users=[]
        for u in list(set([self.expose_user, self.takeover_user, *[c.expose_user for c in self.connected_trades.all()], *[c.takeover_user for c in self.connected_trades.all()]])):
            if (u is not None):
                u.email_user('DienZt-Markt: ' + emailtype + '(' + ts + ')', t.render(context={
                    'Firstname': u.first_name, 'random': str(random.randint(0, 9)), 'add_info': add_info}))
                notified_users.append(u)
        return notified_users








