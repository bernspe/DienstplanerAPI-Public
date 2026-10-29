import datetime

import pandas as pd
from celery import shared_task
from django.db.models import Q
from dienstplan.models import DienstplanData, WishData
from dienstplan.utils import automake_dienstplan_for_institution
from users.models import Institution

from ortools.sat.python import cp_model

@shared_task
def modeldummytask():
    model = cp_model.CpModel()
    a=model.NewBoolVar('a')
    b=model.NewBoolVar('b')
    model.Add(sum([a,b])>=1)
    model.Maximize(sum([a,b]))
    solver = cp_model.CpSolver()
    solver.parameters.linearization_level = 0
    solver.parameters.log_search_progress = False
    #solver.log_callback = print
    try:
        status = solver.Solve(model)
    except Exception as e:
        print(e)

@shared_task
def automator_background():
    tp = datetime.datetime.now()
    for institution in Institution.objects.all():
        d = DienstplanData.objects.filter(institution_link=institution, referringDate__gt=tp,
                                 status__in=['Angelegt', 'Automator'])
        # get the referringDates, make them unique with set
        d_skizze = DienstplanData.objects.filter(institution_link=institution,referringDate__gt=tp,status__in=['Skizze','Geplant'])
        ddates = set([r.referringDate for r in d.all()])
        removedates = set([r.referringDate for r in d_skizze.all()]) # remove all Dienstplans that have a Skizze and are finished Geplant
        d_final_dates = list(ddates - removedates)
        for rdate in d_final_dates:
            ym = rdate.strftime('%Y-%m')
            automake_dienstplan_for_institution(institution.id, ym)