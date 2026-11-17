import model_policy as m

def test_zero_extra_credit_does_not_mean_subscription_exhausted():
 r=m.summarize_codex({'ordinaryUsageAllowed':True,'rateLimits':{'primary':{'usedPercent':84},'credits':{'balance':'0','hasCredits':False}}})
 assert r['status']=='available'
 assert r['windows'][0]['remaining_percent']==16

def test_exhausted_subscription_never_uses_paid_credit_fallback():
 r=m.summarize_codex({'rateLimits':{'primary':{'usedPercent':100},'credits':{'balance':'100','hasCredits':True}}})
 assert r['status']=='exhausted' and r['extra_credits_not_used'] is True

def test_unknown_quota_is_not_claimed_available():
 assert m.summarize_codex({})['status']=='unknown'

def test_antigravity_groups_and_disabled_windows():
 groups=[{'name':'Gemini Models','buckets':[{'remaining_fraction':0,'disabled':False},{'remaining_fraction':.9,'disabled':True}]},
         {'name':'Gemini 4 Argon','buckets':[{'remaining_fraction':.5}]}]
 assert m.summarize_agy(groups,'gemini-4-hydrogen-high')['status']=='exhausted'
 assert m.summarize_agy(groups,'gemini-4-argon-high')['status']=='available'
