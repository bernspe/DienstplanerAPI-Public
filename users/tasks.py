import datetime
import random

from celery import shared_task
from dateutil.relativedelta import relativedelta
from django.template.loader import get_template

from dienstplan.models import DienstplanData
from users.models import Institution, User

@shared_task
def clear_tokens():
    from oauth2_provider.models import clear_expired
    clear_expired()

@shared_task
def check_institutions_dienstplan_schedule_for_denovo_creation():
    # this should be called every day
    all_institutions = Institution.objects.all()
    for institution in all_institutions:
        if institution.data is not None:
            if 'automation' in institution.data.keys():
                currMonth = datetime.datetime.now().month
                currYear = datetime.datetime.now().year
                # check if there needs a new dienstplan to be made
                reqActDate = datetime.date(day=int(institution.data['automation']['dayOfMonthInvitation']), month=currMonth, year=currYear)
                reqDplDate = reqActDate+relativedelta(months=int(institution.data['automation']['numberMonthsAhead']))
                reqDplDateTime=datetime.datetime.combine(reqDplDate, datetime.datetime.min.time(), tzinfo=datetime.timezone.utc)
                today = datetime.date.today()
                laps=(today - reqActDate).days
                # now when the day has come -> make a new Dienstplan and tell everyone
                if laps==0:
                    q = DienstplanData.objects.filter(status='Angelegt', referringDate__month=str(reqDplDate.month),
                                                      referringDate__year=str(reqDplDate.year),
                                                      institution_link=institution)
                    if len(q) == 0:
                        dpl=DienstplanData.objects.create(referringDate=reqDplDateTime,institution_link=institution,status='Angelegt')
                        dpl.autocreateFeiertage()
                        dpl.save()
                        # email to all to collect wishes
                        emailtype = 'generate'
                        emailfolder = 'dienstplan'
                        try:
                            add_info = str(reqDplDate.month)+'/'+str(reqDplDate.year)
                            t = get_template(emailfolder + '/' + emailtype + '.html')
                            ts = str(datetime.datetime.now(datetime.timezone.utc))
                            institution.sendEMailToPhysiciansOfThisInstitution('DienZtliche Mail: ' + emailtype + '(' + ts + ')',
                                                                t.render(context={
                                                                    'add_info': add_info,
                                                                    'random': str(random.randint(0, 9))}))
                        except Exception as e:
                            print('Tried to send invitation email, but : ' + str(e))
                # check if there are physicians who need to be reminded
                reminderDates = [reqActDate+relativedelta(days=(n+1)*int(institution.data['automation']['reminderInterval'])) for n in range(int(institution.data['automation']['reminderMax']))]
                if today in reminderDates:
                    # get all the physicians
                    physician_groups = institution.getPhysiciansOfThisInstitution()
                    phys = User.objects.filter(institution_link=institution, groups__name__in=physician_groups).distinct()
                    for p in phys:
                        w = p.wishdata.filter(yearmonth=str(reqDplDate.year)+'-'+str(reqDplDate.month))
                        if len(w)>0: # just for debugging purposes
                            pass
                        if len(w) == 0:
                            emailtype = 'reminder'
                            emailfolder = 'dienstplan'
                            try:
                                t = get_template(emailfolder + '/' + emailtype + '.html')
                                ts = str(today)
                                p.email_user('DienZtliche Mail: ' + emailtype + '(' + ts + ')',
                                                t.render(context={
                                                    'Firstname': p.first_name,
                                                    'random': str(random.randint(0, 9))}))
                            except Exception as e:
                                print('Tried to send reminder email, but : '+str(e))
