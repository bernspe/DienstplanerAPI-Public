import datetime
import random

import pandas as pd
from django.db.models import Q
from dienstplan.models import DienstplanData, WishData
from users.models import Institution, User
import numpy as np
from ortools.sat.python import cp_model

POSSIBLE_WISHES = ['no','yes','limited','maybe']
def flatten(xss):
    return [x for xs in xss for x in xs]

def generateDienstplanFromWishlist(wishes_df, bootstrap_series = None, distribution_inequality = 2, interval_minimizer=0, wish_encodings={},
                                   physicianMinMaxPreferences=None):
    # wishes_df columns = usernames, index = dates
    # bootstrap index = dates, values = usernames who are assigned to Dienst at that date already
    # wish_encodings -> to adjust the weights
    # physicianMinMaxPreferences = dict with usernames as keys: {min: number, max: number} of allowed Dienst assignments
    if physicianMinMaxPreferences is None:
        physicianMinMaxPreferences = {}
    if bootstrap_series is not None:
        for (r,c) in zip(bootstrap_series.index.to_list(),bootstrap_series.to_list()):
            wishes_df.at[r,c]='mandatory'
    wish_encodings = {'no': 0, 'limited': 1, 'maybe': 2, 'yes': 3, 'mandatory': 4, **wish_encodings}
    doctor_wishlist={username: [wish_encodings[v] for v in wishlist.values()] for (username,wishlist) in wishes_df.to_dict().items()}
    model = cp_model.CpModel()
    # [END model]

    # Creates the variables.
    # [START variables]
    shifts = {}
    all_doctors = list(doctor_wishlist.keys())
    num_doctors = len(all_doctors)
    num_shifts = 1
    num_days=len(wishes_df.index)
    all_days = range(num_days)
    weekdays = wishes_df.index.weekday.to_list()

    for n in all_doctors:
        for d in all_days:
            shifts[(n, d)] = model.NewBoolVar('shift_n%s%is' % (n, d))
    # [END variables]

    # Creates the constraints.
    # [START constraints]
    for d in all_days:
        model.Add(sum(wishes[d]*shifts[(n,d)] for n, wishes in doctor_wishlist.items())>=1) # there should be at least one per date
        model.Add(sum(shifts[(n,d)] for n, wishes in doctor_wishlist.items() ) <= 1) # only one per date

    # no consecutive shifts
    for n in all_doctors:
        for d in all_days:
            if d == 0:
                continue
            else:
                model.Add((shifts[(n, d - 1)] + shifts[(n, d)]) <= 1)


    min_shifts_per_doctor = (num_shifts * num_days) // num_doctors

    if num_shifts * num_days % num_doctors == 0:
        max_shifts_per_doctor = min_shifts_per_doctor
    else:
        max_shifts_per_doctor = min_shifts_per_doctor + 1
    # To distribute the shifts evenly among doctors if there are no preferences
    for n in all_doctors:
        if n in physicianMinMaxPreferences.keys():
            days_between_shifts = num_days // physicianMinMaxPreferences[n]['max']

            # distribute shifts evenly
            model.Add(sum(shifts[(n, d)] for d in all_days) >= physicianMinMaxPreferences[n]['min'])
            model.Add(sum(shifts[(n, d)] for d in all_days) <= physicianMinMaxPreferences[n]['max'])
            if 'we' in physicianMinMaxPreferences[n].keys(): # set weekend-max if given
                model.Add(sum(shifts[(n, d)] for d in all_days if weekdays[d] > 4) <= physicianMinMaxPreferences[n]['we'])
            else: # set weekend max to 2
                model.Add(sum(shifts[(n, d)] for d in all_days if weekdays[d] > 4) <= 2)

        else:
            days_between_shifts = num_days // min_shifts_per_doctor

            #distribute evenly between doctors
            model.Add(sum(shifts[(n, d)] for d in all_days) >= min_shifts_per_doctor-distribution_inequality)
            model.Add(sum(shifts[(n, d)] for d in all_days) <= max_shifts_per_doctor+distribution_inequality)
            model.Add(sum(shifts[(n, d)] for d in all_days if weekdays[d] > 4) <= 2)

        # perhaps to correct interval if no solutions can be obtained
        if interval_minimizer < days_between_shifts:
            days_between_shifts=days_between_shifts-interval_minimizer
        # try to expand time between shifts
        for d in all_days:
            for i in range(2, days_between_shifts - 2):
                if d < i:
                    continue
                else:
                    model.Add((shifts[(n, d - i)] + shifts[(n, d)]) <= 1)

    # [END constraints]

    # [START objective]
    model.Maximize(sum([wishes[d]*shifts[(n,d)] for n, wishes in doctor_wishlist.items() for d in all_days]))

    # [END objective]

    # Creates a solver and solves the model.
    # [START solve]
    solver = cp_model.CpSolver()
    solver.parameters.linearization_level = 0
    solver.parameters.log_search_progress = False
    #solver.log_callback = print
    status = solver.Solve(model)
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        solver.parameters.enumerate_all_solutions = True

        class DoctorsPartialSolutionPrinter(cp_model.CpSolverSolutionCallback):
            """Print intermediate solutions."""

            def __init__(self, shifts, num_doctors, num_days, limit, docs):
                cp_model.CpSolverSolutionCallback.__init__(self)
                self._shifts = shifts
                self._num_doctors = num_doctors
                self._num_days = num_days
                self._solution_count = 0
                self._solution_limit = limit
                self._docs =docs
                self.result=[]

            def on_solution_callback(self):
                self._solution_count += 1
                final_shifts =[]
                temp1 = []
                temp5 = []
                for i in range(self._num_doctors):
                    final_shifts.append([])

                for d in range(self._num_days):
                    #temp1.append('Day %i' % (d+1))
                    cal_date = wishes_df.index[d].strftime('%d.%m.%Y')
                    temp1.append(cal_date)
                    temp3= '' # has been changed from []

                    for n in range(self._num_doctors):
                        if self.Value(self._shifts[(self._docs[n], d)]):
                            #temp3.append(self._docs[n])
                            temp3=self._docs[n]
                            final_shifts[n].append(d)

                    temp5.append(temp3)

                final_roster = {i: j for i, j in zip(temp1, temp5)}
                # check distribution of doctors
                # calculates the minimum distance between Diensts in days and returns a dict
                min_dist = {idx: np.min(np.diff(v)) for idx, v in
                            {k: [idxx for idxx, (kk, vv) in enumerate(final_roster.items()) if k in vv] for k in
                             flatten(final_roster.values())}.items() if len(v) > 1}
                # the median of Dienst_distances over every doctor
                median_dist = np.median(list(min_dist.values()))

                self.result.append({'median': median_dist, 'roster': final_roster})

                if self._solution_count >= self._solution_limit:
                    # print('Stop search after %i solutions' % self._solution_limit)
                    ff = []
                    fx = []
                    for i in final_shifts:
                        i.sort()
                        ff.append(len(i))

                    self.StopSearch()

                return self.result

            def solution_count(self):
                return self._solution_count

        # Display the first solution.
        solution_limit = 20
        solution_printer = DoctorsPartialSolutionPrinter(shifts, num_doctors, num_days, solution_limit,all_doctors)

        solver.Solve(model, solution_printer)

        return solution_printer.on_solution_callback()
    else:
        return({})


def getLastWishes(groupname, referring_date, institution):
    employees_of_my_institution = institution.employee.filter(groups__name=groupname).order_by('last_name')
    participating_users = employees_of_my_institution
    lastwishes = []
    nonwishers = []
    referring_date_timestamp = None
    if referring_date:
        referring_date_timestamp = pd.Timestamp(referring_date).astimezone(None)
    for user in participating_users:
        if referring_date_timestamp:
            found_a_wish = None
            ym = referring_date_timestamp.strftime('%Y-%m')
            # fast search
            wishquery = user.wishdata.filter(yearmonth=ym)
            if len(wishquery) > 0:
                found_a_wish = wishquery.latest('created').to_series()
            else:
                # slow search if yearmonth field is not filled
                for wish in user.wishdata.order_by('-created'):
                    ym_c = wish.getYearMonth()  # this also fills the field to speed up for next time
                    if ym_c == ym:
                        found_a_wish = wish.to_series()
                        break
            if found_a_wish is not None:
                lastwishes.append(found_a_wish)
            ## create empty series for not existing wishes
            else:
                min = pd.offsets.MonthBegin().rollback(referring_date_timestamp)
                max = referring_date_timestamp + pd.offsets.MonthEnd()
                dti = pd.date_range(start=min, end=max, freq='D', normalize=True)
                s = pd.Series(index=dti, name=str(user.username))
                nonwishers.append(user.username)
                lastwishes.append(s)
        else:
            lastwishes.append(user.wishdata.latest('created').to_series())
    return lastwishes, nonwishers, participating_users

def formatRoster2Dpl(group, roster):
    a=[]
    for date,username in roster.items():
        lastname=''
        try:
            u=User.objects.get(username=username)
            lastname=u.last_name+', '+u.first_name
        except:
            pass
        a.append({'date':date,'username':username,'lastname':lastname})
    return {'group':group,'Dienstplan':a}

def makeYesBootstrapFromWishes(df_wishes):
    pass

def automake_dienstplan_for_institution(institution_str, yearmonth):
    try:
        institution=Institution.objects.get(id=institution_str)
        dpl = DienstplanData.objects.filter(institution_link=institution, referringDate__year=yearmonth.split('-')[0], referringDate__month=yearmonth.split('-')[1])
        dpl_angelegt = dpl.filter(status='Angelegt')
        if len(dpl_angelegt) == 1:
            # only proceed if there is an angelegt dienstplan
            dpl_angelegt = dpl_angelegt.last()
            dpl_automated,_ = DienstplanData.objects.get_or_create(status='Automator',institution_link=institution, referringDate=dpl_angelegt.referringDate)
            print('Automator Dienstplan zugegriffen')
            physician_groups = institution.data.get('automation',{}).get('groups', institution.getPhysiciansOfThisInstitution())
            data=institution.data
            wish_encoder = {}
            dienstplaene = []
            if 'wishCalculationParams' in data.keys():
                wish_encoder = dict(data['wishCalculationParams'])
                wish_encoder = {k: int(v) for (k, v) in wish_encoder.items()}  # convert to int
            for group in physician_groups:
                referring_date = datetime.datetime(year=int(yearmonth.split('-')[0]),month=int(yearmonth.split('-')[1]),day=1,tzinfo=datetime.UTC)
                lastwishes, nonwishers, participating_users = getLastWishes(group, referring_date, institution)
                if len(lastwishes)>0:
                    df = pd.concat(lastwishes, axis=1, keys=[s.name for s in lastwishes]).fillna('maybe')
                    o={}
                    for p in participating_users.filter(data_dienzt_preferences__isnull=False):
                        try:
                            o[p.username] = {'min': p.data_dienzt_preferences['dienstPreferences'][group][0],
                                             'max': p.data_dienzt_preferences['dienstPreferences'][group][1],
                                             'we': p.data_dienzt_preferences['dienstPreferences'][group][2]}
                        except:
                            pass
                    dpls = generateDienstplanFromWishlist(df,
                                                          wish_encodings=wish_encoder,
                                                          physicianMinMaxPreferences=o)
                    if len(dpls)>0:
                        dpls.sort(key=lambda x: x['median'], reverse=True)
                        result = dpls[0]['roster']
                        dienstplaene.append(formatRoster2Dpl(group,result))
            dpl_angelegt_data = dpl_angelegt.data | {}
            dpl_automated.data={**dpl_angelegt_data,'Dienstplaene':dienstplaene}
            dpl_automated.save()
            return dpl_automated
    except Exception as e:
        print(e)

def automake_dienstplan_for_anonymous(namelist, yearmonth, distribution_inequality, interval_minimizer):
    referring_date = datetime.datetime(year=int(yearmonth.split('-')[0]), month=int(yearmonth.split('-')[1]), day=1,
                                       tzinfo=datetime.UTC)
    referring_date_timestamp = pd.Timestamp(referring_date).astimezone(None)
    min = pd.offsets.MonthBegin().rollback(referring_date_timestamp)
    max = referring_date_timestamp + pd.offsets.MonthEnd()
    dti = pd.date_range(start=min, end=max, freq='D', normalize=True)
    wisharray=[]
    for n in namelist:
        s = pd.Series(index=dti, name=n)
        wisharray.append(s)
    df = pd.concat(wisharray, axis=1, keys=[s.name for s in wisharray]).fillna('maybe')
    dpls = generateDienstplanFromWishlist(df, distribution_inequality=distribution_inequality, interval_minimizer=interval_minimizer)
    return dpls