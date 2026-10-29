import base64
import datetime
import hashlib
import json
import random
import string
import uuid
import htmlmin
import pandas as pd
import requests
import yagmail
from django.contrib.auth.models import AbstractUser
from encrypted_json_fields import fields

import smtplib
import dkim
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.db import models
from django.utils.translation import gettext_lazy as _

from DienstplanerAPI import settings
from DienstplanerAPI.settings import DEFAULT_EXPIRY, EMAIL_FROM, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, \
    ADMINISTRATION_GROUPS, EMAIL_RELAY, BASIC_GROUPS, WOCHENTAGE_ARBEITSZEITEN_DEFAULT, EMAIL_HOST, \
    DKIM_PRIVATE_KEY_FILE, DKIM_SELECTOR, PRIVACYIDEA_HOST, DIENZT_REALM, PI_OTP_PIN


class DictObj:
    def __init__(self, in_dict: dict):
        assert isinstance(in_dict, dict)
        for key, val in in_dict.items():
            if isinstance(val, (list, tuple)):
                setattr(self, key, [DictObj(x) if isinstance(x, dict) else x for x in val])
            else:
                setattr(self, key, DictObj(val) if isinstance(val, dict) else val)


def get_default_expiry():
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=DEFAULT_EXPIRY)


PI_STATUS = (
    ('NO', 'No 2FA'),
    ('TRIGGERED', '2FA Rollout triggered'),
    ('2FA', '2FA successful'),
    ('VALID', '2FA validated')
)


def send_email(
        to_email,
        sender_email,
        subject,
        message_html,
        dkim_private_key_path=DKIM_PRIVATE_KEY_FILE,
        dkim_selector=DKIM_SELECTOR,
):
    # the `email` library assumes it is working with string objects.
    # the `dkim` library assumes it is working with byte objects.
    # this function performs the acrobatics to make them both happy.
    message_text = 'Diese Email kann nur als HTML angezeigt werden. Bitte aktivieren Sie HTML in den EMail-Einstellungen.'
    if isinstance(message_text, bytes):
        # needed for Python 3.
        message_text = message_text.decode()

    if isinstance(message_html, bytes):
        # needed for Python 3.
        message_html = message_html.decode()

    sender_domain = sender_email.split("@")[-1]

    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(message_text, "plain"))
    msg.attach(MIMEText(message_html, "html"))
    if isinstance(to_email, list):
        to_email_msg = ','.join(to_email)
    else:
        to_email_msg = to_email
    msg["To"] = to_email_msg
    msg["From"] = sender_email
    msg["Subject"] = subject
    msg_data = msg.as_bytes()

    if dkim_private_key_path and dkim_selector:
        # the dkim library uses regex on byte strings so everything
        # needs to be encoded from strings to bytes.
        with open(dkim_private_key_path) as fh:
            dkim_private_key = fh.read()
        headers = [b"To", b"From", b"Subject"]
        sig = dkim.sign(
            message=msg_data,
            selector=str(dkim_selector).encode(),
            domain=sender_domain.encode(),
            privkey=dkim_private_key.encode(),
            include_headers=headers,
        )
        # add the dkim signature to the email message headers.
        # decode the signature back to string_type because later on
        # the call to msg.as_string() performs it's own bytes encoding...
        msg["DKIM-Signature"] = sig[len("DKIM-Signature: "):].decode()
        msg_data = msg.as_bytes()

    with smtplib.SMTP_SSL(EMAIL_HOST) as s:
        s.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
        s.sendmail(sender_email, to_email, msg_data)
        s.quit()
    return msg


# Create your models here.
class User(AbstractUser):
    username = models.CharField(_('username'), max_length=100, unique=True, primary_key=True, default=str(uuid.uuid4()))
    date_joined = models.DateTimeField(_('date joined'), auto_now_add=True)
    last_login = models.DateTimeField(_('last login'), null=True, blank=True)
    institution_link = models.ForeignKey('Institution', related_name='employee', on_delete=models.CASCADE, null=True,
                                         blank=True)
    allows_payment_data = models.BooleanField(default=False)
    data_dienzt_preferences = models.JSONField(blank=True, null=True)
    data_encrypted = fields.EncryptedJSONField(null=True, blank=True)
    pi_passkey = models.CharField(_('pi passkey'), max_length=100, blank=True)
    pi_status = models.CharField(choices=PI_STATUS, max_length=10, default='NO')
    pi_serialnumber = models.CharField(_('pi serial'), max_length=30, blank=True)
    pi_accesstoken = fields.EncryptedJSONField(null=True, blank=True)
    emailtoken = models.CharField(_('email token'), max_length=6, blank=True)
    is_emailvalidated = models.BooleanField(default=False)
    additional_flags = models.JSONField(blank=True, null=True)

    REQUIRED_FIELDS = []

    def create_pi_passkey(self, lastlogin):
        """
        creates the PI passkey from last_login
        :return: SHA-encrypted hash, raw
        """
        ll = bytes(str(lastlogin.date()), 'ascii')
        m = hashlib.sha1(ll)
        hashed = '{SHA}' + base64.b64encode(m.digest()).decode('ascii')
        self.pi_passkey = hashed
        self.save()
        return hashed, str(lastlogin.date())

    def auth_at_privacyidea(self, force=False):
        """
        Auth at PI Server and save accesstoken at DB
        :param force: force to issue new auth token
        :return:
        """
        authuri = PRIVACYIDEA_HOST + 'auth'
        _, password = self.create_pi_passkey(self.last_login)
        max_tries = 5
        counter = 0
        status = 400
        auth_params = {"username": self.email, "password": password, "realm": DIENZT_REALM}
        while (counter < max_tries) and (status > 200):
            print('Auth trial %i and status %i' % (counter, status))
            print('Trying to auth user %s with password %s' % (self.email, password))
            r_auth = requests.post(authuri, data=json.dumps(auth_params))
            status = r_auth.status_code
            counter += 1
            if status == 200:
                print('Successful auth at PI Server')
                response_after_auth = json.loads(r_auth.content)
                self.pi_accesstoken = response_after_auth
                self.save()
                return status, response_after_auth
        return status, {}

    def delete_current_pi_token(self, token=None):
        """
        Deletes 2FA OTP
        :param token: the accesstoken to auth at pi server
        :return: the status of the http delete request at pi server
        """
        try:
            if self.pi_serialnumber == '':
                delresponse = DictObj({'content': 'No Serialnumber', 'status_code': 400})
            else:
                deluri = PRIVACYIDEA_HOST + 'token/' + self.pi_serialnumber

                if token is None:
                    token = self.pi_accesstoken['result']['value']['token']

                headers = {
                    'Pi-Authorization': token,
                }
                print('Approaching delete routine for serial %s' % self.pi_serialnumber)
                delresponse = requests.delete(deluri, headers=headers)
        except Exception as e:
            delresponse = DictObj({'content': 'Error at del routine ' + str(e), 'status_code': 400})
        self.pi_serialnumber = ''
        self.pi_status = 'NO'
        self.pi_passkey = ''
        self.save()
        return delresponse

    def validate_mfa_token(self, token):
        validationuri = PRIVACYIDEA_HOST + 'validate/check'
        email = self.email
        params = {'user': email, 'pass': PI_OTP_PIN + token, 'realm': DIENZT_REALM}
        r = requests.post(validationuri, data=json.dumps(params))
        return r

    def email_user(self, subject, message, from_email=EMAIL_FROM, **kwargs):
        '''
        Sends an email to this User.
        '''
        msg_minified = htmlmin.minify(message)
        if EMAIL_RELAY == 'gmail':
            yagmail.SMTP(settings.GMAIL_USERNAME, settings.GMAIL_PASSWORD).send(self.email, subject, msg_minified)
        if EMAIL_RELAY == 'renecol':
            send_email(self.email, from_email, subject, msg_minified)

    def get_physician_groups_of_my_institution(self):
        # get the users of this institution
        myinstitution = self.institution_link
        if not myinstitution:
            return None
        physician_groups = myinstitution.getPhysiciansOfThisInstitution()
        return physician_groups

    def get_planer_groups_of_my_institution(self):
        # get the users of this institution
        myinstitution = self.institution_link
        if not myinstitution:
            return []
        queryset = User.objects.filter(institution_link=myinstitution, groups__name='Planer').all()
        return queryset

    def email_physicians_of_my_institution(self, subject, message, from_email=EMAIL_FROM, **kwargs):
        '''
        Sends an email to physicians of this institution.
        '''
        myinstitution = self.institution_link
        myinstitution.sendEMailToPhysiciansOfThisInstitution(subject, message, from_email)

    def email_everyone_of_my_institution(self, subject, message, from_email=EMAIL_FROM, **kwargs):
        '''
        Sends an email to this User.
        '''
        msg_minified = htmlmin.minify(message)
        # get the users of this institution
        myinstitution = self.institution_link
        queryset = User.objects.filter(institution_link=myinstitution)
        email_adresses = [q.email for q in queryset]
        if EMAIL_RELAY == 'gmail':
            yagmail.SMTP(settings.GMAIL_USERNAME, settings.GMAIL_PASSWORD).send(email_adresses, subject, msg_minified)
        if EMAIL_RELAY == 'renecol':
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = from_email
            msg['To'] = email_adresses
            msg.set_content(msg_minified, 'html')
            with smtplib.SMTP_SSL(EMAIL_HOST) as s:
                s.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
                s.send_message(msg)
                s.quit()

    def create_email_token(self):
        """
        Creates a 6digit Token to be sent via email
        :return:
        """
        # letters = string.ascii_letters
        letters = string.digits
        result_str = ''.join(random.choice(letters) for i in range(6))
        return result_str

    def allDiensts(self):
        """
        returns all the Diensts of the specified user as a series
        :return:
        """
        df = self.institution_link.latestValidDienstplaene_asDf()
        df2 = df[df['username'] == self.username]
        df3 = df2.drop(columns=['username', 'lastname'])
        df3.index = pd.to_datetime(df2['date'], format='%d.%m.%Y')
        return df3.drop(columns=['date'])

    def getVertragsArbeitszeiten(self):
        d = self.data_encrypted
        if not d:
            return WOCHENTAGE_ARBEITSZEITEN_DEFAULT
        k = 'Vertrags_Arbeitszeiten'
        if k in d.keys():
            return d[k]
        else:
            return WOCHENTAGE_ARBEITSZEITEN_DEFAULT

    def retrieveAbrechnungsMatrix(self):
        """
        Liefert anhand der verfügbaren validen (=letzte pro Monat geplante) Dienstpläne die Einsatzmatrix des Benutzers zurück als Dataframe - datetimeindex
        :return:
        """
        # letzte Dienstpläne zusammenfügen, Dataframe mit Usernames und nicht mit Lastnames zurückgeben
        lvd = self.institution_link.latestValidDienstplaene()
        a = []
        for dpl in lvd:
            d = dpl.to_dataframe(how='username', other='lastname')
            a.append(d)
        dfg = pd.concat(a)
        # Die Dienstgruppen speichern
        l_arztgroups = dfg.columns.tolist()
        [l_arztgroups.remove(x) for x in ['Datum', 'Wochentag', 'Feiertag']]
        # Dienstcolum = place the group name (e.g. Arzt...) at the DienstDay of the username
        dfg['Dienst'] = dfg.apply(lambda row: row[row == self.username].index.tolist(), axis=1)
        # Die üblichen Arbeitszeiten des Arztes anhand der Wochentage und des hinterlegten Schemas eintragen
        contracted_hours = self.getVertragsArbeitszeiten()
        dfg['VertragsArbeitszeiten'] = dfg['Wochentag'].map(contracted_hours)
        dfg['VertragsArbeitszeiten'] = dfg['VertragsArbeitszeiten'].transform(float)
        try:
            fzar = self.institution_link.getFZARequirement()
            arbzgh = self.institution_link.getArbZGStundenOfDienstmodell()
        except Exception as e:
            return

        # Unterfunktionen: erforderlich für die nächsten Schritte
        def retrieveDiensthours(groups, weekday):
            a = []
            b = []
            for g in groups:
                if g in arbzgh.keys():
                    hours = str(arbzgh[g][weekday])
                    if len(hours) == 0:
                        a.append(int(0))
                    else:
                        a.append(float(hours))
                    try:
                        b.append(fzar[g])
                    except:  # if Dienstgruppe not found in dict set FZA-Requirement false
                        b.append(False)
            if len(a) > 0:
                return [max(a), max(b)]
            else:
                return [0, False]

        def createRelleArbeitsstunden(df):
            df[['Dienststunden', 'FZA']] = df[['Dienst', 'Wochentag']].apply(
                lambda x: retrieveDiensthours(x.iloc[0], x.iloc[1]), axis=1, result_type='expand')
            df['FZA'] = dfg['FZA'].shift()
            df['ReelleArbeitsstunden'] = df[['VertragsArbeitszeiten', 'Dienststunden']].aggregate(['sum'], axis=1)
            mask = df[df['FZA'].fillna(False)].index
            df.loc[mask, 'ReelleArbeitsstunden'] = 0
            return df

        dfg = createRelleArbeitsstunden(dfg)

        def includeDienstmodell(df):
            dienstmodell = self.institution_link.getDienstmodell()
            df['Dienstmodell'] = df[['Dienst', 'Wochentag']].apply(
                lambda x: [dienstmodell[group][x.iloc[1]] for group in x.iloc[0]], axis=1)
            df['Dienstmodell'] = df['Dienstmodell'].apply(lambda x: ' + '.join(x))
            return df

        dfg = includeDienstmodell(dfg)

        from dienstplan.models import make_social_score_from_dates
        dfg['social_score'] = dfg.apply(
            lambda x: make_social_score_from_dates(x['Wochentag'], x['Feiertag'], dienst=len(x['Dienst'])), axis=1)

        dfg.drop(columns=l_arztgroups + ['FZA', 'Dienststunden'], inplace=True)

        return dfg


class Institution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    expires = models.DateTimeField(default=get_default_expiry())
    name = models.CharField(_('name'), max_length=100, unique=True, blank=False, null=False)
    state_isocode = models.CharField(_('state isocode'), max_length=10, unique=False, blank=True, null=True)
    logo_url = models.URLField(max_length=200, blank=True, null=True)
    codename = models.CharField(_('codename'), max_length=100, unique=True, blank=True,
                                null=True)  # erlaubt das automatische Anmelden in der Institution für Benutzer, die dieses Suffix in ihrer eMail tragen
    data = models.JSONField(null=True, blank=True)

    def has_expired(self):
        present = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc)
        return present > self.expires

    def __str__(self):
        return self.name

    # returns all the Dienstplaene of an institution with status='Geplant' and latest
    def latestValidDienstplaene(self):
        # d = self.dienstplan.filter(status='Geplant')
        d = self.dienstplan.all()
        # get the referringDates, make them unique with set
        rdates = list(set([r.referringDate for r in d.all()]))
        ids = []
        for rdate in rdates:
            dpl = d.filter(referringDate=rdate).order_by('-created')[0]
            ids.append(dpl.id)
        return d.filter(id__in=ids)

    def latestValidDienstplaene_asDf(self):
        dpl_objs = self.latestValidDienstplaene()
        dfs = []
        for d in dpl_objs:
            dienstplaene = d.data['Dienstplaene']
            for dienstplan in dienstplaene:
                group = dienstplan['group']
                df_partial = pd.DataFrame(dienstplan['Dienstplan'])
                df_partial = df_partial.assign(group=group)
                dfs.append(df_partial)
        result = pd.concat(dfs)
        return result

    def getDienstmodell(self):
        d = self.data
        k = 'Dienstmodell'
        if k in d.keys():
            return d[k]

    def getArbZGStundenOfDienstmodell(self):
        """
        liefert die Stunden zurück, die ein Arzt im Dienst (key = Gruppe) physisch in der Klinik anwesend sein muss
        :return:
        """
        d = self.data
        k = 'Anwesenheitstunden'
        if k in d.keys():
            return d[k]

    def getFZARequirement(self):
        """
        Ermittelt, ob eine Dienstgruppe, die Pflicht zum Freizeitausgleich am Folgetag (zB 24h Dienst) nach sich zieht
        :return:
        """
        d = self.data
        k = 'Fza_Erfordernis'
        if k in d.keys():
            return d[k]

    def getPhysiciansSocialScores(self, ym=None):
        """
        Liefert die SocialScore Wish-Statistik der Institutions-Physicians zurück
        :param ym: YYYY-MM des betreffenden Monats
        :return:
        """
        user_scores = 0
        mean_score = 0
        std_score = 0
        if ym is not None:
            from dienstplan.models import WishData
            import statistics
            queryset = User.objects.filter(institution_link=self.id)
            # get the physicians of this institution
            all_groups = list(set(sum([[g.name for g in u.groups.all()] for u in queryset], [])))
            physician_groups = [x for x in all_groups if x not in (ADMINISTRATION_GROUPS + BASIC_GROUPS)]
            physician_queryset = User.objects.filter(institution_link=self.id, groups__name__in=physician_groups)
            wishes = WishData.objects.filter(owner__in=physician_queryset, yearmonth=ym).all()
            user_scores = {wish.owner.username: wish.getSocialScore() for wish in wishes}
            values = user_scores.values()
            if len(values) > 1:
                mean_score = int(statistics.mean(values))
                std_score = statistics.stdev(values)
        return {'user_scores': user_scores, 'mean_score': mean_score, 'stddev': std_score}

    def getPhysiciansOfThisInstitution(self):
        """
        Get the physician groups of this institution
        :return: list of physician groups as strings
        """
        queryset = User.objects.filter(institution_link=self)
        # get the physicians of this institution
        all_groups = list(set(sum([[g.name for g in u.groups.all()] for u in queryset], [])))
        return [x for x in all_groups if x not in (ADMINISTRATION_GROUPS + BASIC_GROUPS)]

    def sendEMailToPhysiciansOfThisInstitution(self, subject, message, from_email=EMAIL_FROM):
        msg_minified = htmlmin.minify(message)
        physician_groups = self.getPhysiciansOfThisInstitution()
        queryset = User.objects.filter(institution_link=self, groups__name__in=physician_groups)
        email_adresses_of_physicians = [q.email for q in queryset]
        if len(email_adresses_of_physicians) == 0:
            return
        if EMAIL_RELAY == 'gmail':
            yagmail.SMTP(settings.GMAIL_USERNAME, settings.GMAIL_PASSWORD).send(email_adresses_of_physicians, subject,
                                                                                msg_minified)
        if EMAIL_RELAY == 'renecol':
            send_email(email_adresses_of_physicians, from_email, subject, msg_minified)


class Abrechnung(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    yearmonth = models.CharField(max_length=20, null=True, blank=True)  # Bezug zu welchem Dienstmonat
    institution_link = models.ForeignKey('Institution', related_name='abrechnung', on_delete=models.CASCADE, null=True,
                                         blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='meine_abrechnung', on_delete=models.CASCADE,
                              null=True,
                              blank=True)
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='validierte_abrechnung',
                                     on_delete=models.SET_NULL, null=True,
                                     blank=True)
    validated_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default="Erstellt")
    data_stunden_encrypted = fields.EncryptedJSONField(null=True, blank=True)
    data_payment_encrypted = fields.EncryptedJSONField(null=True, blank=True)


class DienstModell(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    expires = models.DateTimeField(default=get_default_expiry())
    name = models.CharField(_('name'), max_length=100, unique=False, default='Mein Dienstmodell')
    institution_link = models.ForeignKey('Institution', related_name='dienstmodell', on_delete=models.CASCADE,
                                         null=True,
                                         blank=True)
    data = models.JSONField(null=True, blank=True)

    def has_expired(self):
        present = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc)
        return present > self.expires
