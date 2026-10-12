from app.league_parser import parse_time_to_seconds, select_match
from app.periods import determine_match_half

def match(name,time): return {'href':'/'+name,'team_1':name,'team_2':'X','is_live':False,'game_time':time,'status':'UNKNOWN'}
def test_earliest_upcoming_is_not_dom_first():
    items=[match('Arsenal','12:51'),match('Liverpool','22:51'),match('Lombardy','04:38'),match('Manchester','02:51')]
    assert select_match(items)[0]['team_1']=='Manchester'
def test_time_parser():
    assert [parse_time_to_seconds(x) for x in ('00:30','02:59','03:00','03:01','04:20','05:59')]==[30,179,180,181,260,359]
def test_rush_periods():
    assert determine_match_half('02:59')[0]=='1'
    assert determine_match_half('03:00')[0]=='UNKNOWN'
    assert determine_match_half('03:01')[0]=='2'
    assert determine_match_half('04:20')[0]=='2'
    assert determine_match_half('03:00','Перерыв')[0]=='BREAK'
def test_nonzero_unknown_card_is_live_fallback():
    items=[{'href':'/a','team_1':'Арсенал','team_2':'Фенербахче','is_live':False,'game_time':None,'status':'UNKNOWN'}, {'href':'/m','team_1':'Манчестер Сити','team_2':'Бавария','is_live':True,'game_time':None,'status':'LIVE'}]
    assert select_match(items)[0]['team_1']=='Манчестер Сити'
