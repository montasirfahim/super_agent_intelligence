"""Full reset cycle verification:
1. Inject 30 transactions (above 15-txn limit)
2. Verify totals/direction showing drained state
3. Call /api/simulate/reset
4. Verify all sub-info (deductions, additions, net, count, direction) reset
5. Verify all balance cards return to JSON-seed values
6. Verify NO simlive_ txns remain
"""
import urllib.request, json, sys
sys.stdout.reconfigure(encoding='utf-8')

BASE = 'http://localhost:8000'


def get(p):
    return json.loads(urllib.request.urlopen(BASE + p, timeout=10).read())


def post(p, body):
    req = urllib.request.Request(BASE + p, data=json.dumps(body).encode(),
                                 headers={'Content-Type':'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


# Pick a clean agent
agents = get('/api/lookup/agents')
aid = None
for a in agents:
    pre = get(f'/api/dashboard/agent?role=agent&user_id={a["agent_id"]}')
    if pre['physical']['tx_count_total'] == 0:
        aid = a['agent_id']
        break
print(f'using clean agent: {aid}')

# === PHASE 1: Inject 30 transactions ===
print('\n=== PHASE 1: Inject 30 txns ===')
for i in range(25):
    prov = ['1', '2', '3'][i % 3]
    post('/api/simulate/inject-transaction', {
        'agent_id': aid, 'provider_id': prov,
        'type': 'CASH_OUT', 'amount': 5000,
    })
for _ in range(5):
    post('/api/simulate/inject-transaction', {
        'agent_id': aid, 'provider_id': '1',
        'type': 'CASH_IN', 'amount': 2000,
    })

# Verify state after 30 injections
post_inject = get(f'/api/dashboard/agent?role=agent&user_id={aid}')
print(f'After 30 injections:')
print(f'  physical balance: {post_inject["physical"]["balance_fmt"]}')
print(f'  physical totals: in={post_inject["physical"]["cash_in_fmt"]} out={post_inject["physical"]["cash_out_fmt"]} net={post_inject["physical"]["net_fmt"]} count={post_inject["physical"]["tx_count_total"]}')
print(f'  physical direction: {post_inject["physical"]["direction"]}')
for w in post_inject['wallets']:
    print(f'  {w["provider_name"]}: balance={w["balance_fmt"]} out={w["cash_out_fmt"]} net={w["net_fmt"]} count={w["tx_count_total"]} dir={w["direction"]}')

# Verify red/draining state
ok1 = post_inject['physical']['tx_count_total'] == 30
ok2 = post_inject['physical']['direction'] == 'DRAINING'
print(f'  [phase1 check] tx_count=30: {ok1}, direction=DRAINING: {ok2}')

# === PHASE 2: Reset ===
print('\n=== PHASE 2: Call /api/simulate/reset ===')
r = post('/api/simulate/reset', {'agent_id': aid})
print(f'  response: {r}')

# === PHASE 3: Verify clean state ===
post_reset = get(f'/api/dashboard/agent?role=agent&user_id={aid}')
print(f'\nAfter reset:')
print(f'  physical balance: {post_reset["physical"]["balance_fmt"]}')
print(f'  physical totals: in={post_reset["physical"]["cash_in_fmt"]} out={post_reset["physical"]["cash_out_fmt"]} net={post_reset["physical"]["net_fmt"]} count={post_reset["physical"]["tx_count_total"]}')
print(f'  physical direction: {post_reset["physical"]["direction"]}')
for w in post_reset['wallets']:
    print(f'  {w["provider_name"]}: balance={w["balance_fmt"]} out={w["cash_out_fmt"]} net={w["net_fmt"]} count={w["tx_count_total"]} dir={w["direction"]}')

# Critical checks
with open('base_dataset.json') as f:
    data = json.load(f)
expected_physical = float(next(a for a in data['agent'] if a['agent_id'] == aid)['shared_physical_cash'])
expected_wallets = {
    '1': float(next(w for w in data['providerwallet'] if w['agent_id'] == aid and w['provider_id'] == 'bkash')['e_money_balance']),
    '2': float(next(w for w in data['providerwallet'] if w['agent_id'] == aid and w['provider_id'] == 'nagad')['e_money_balance']),
    '3': float(next(w for w in data['providerwallet'] if w['agent_id'] == aid and w['provider_id'] == 'rocket')['e_money_balance']),
}

checks = []
checks.append(('physical balance == JSON seed',
               abs(post_reset['physical']['balance'] - expected_physical) < 0.01))
checks.append(('physical cash_in == 0',
               post_reset['physical']['cash_in_total'] == 0))
checks.append(('physical cash_out == 0',
               post_reset['physical']['cash_out_total'] == 0))
checks.append(('physical net == 0',
               post_reset['physical']['net_total'] == 0))
checks.append(('physical tx_count == 0',
               post_reset['physical']['tx_count_total'] == 0))
checks.append(('physical direction == STABLE',
               post_reset['physical']['direction'] == 'STABLE'))

for w in post_reset['wallets']:
    pid = w['provider_id']
    checks.append((f'{w["provider_name"]} balance == JSON seed',
                   abs(w['balance'] - expected_wallets[pid]) < 0.01))
    checks.append((f'{w["provider_name"]} cash_out == 0',
                   w['cash_out_total'] == 0))
    checks.append((f'{w["provider_name"]} cash_in == 0',
                   w['cash_in_total'] == 0))
    checks.append((f'{w["provider_name"]} net == 0',
                   w['net_total'] == 0))
    checks.append((f'{w["provider_name"]} count == 0',
                   w['tx_count_total'] == 0))
    checks.append((f'{w["provider_name"]} direction == STABLE',
                   w['direction'] == 'STABLE'))

print('\n--- Verification ---')
all_ok = True
for label, ok in checks:
    print(f'  [{"✓" if ok else "✗"}] {label}')
    if not ok:
        all_ok = False

# DB-level sanity check
from sqlmodel import Session, select
from app.database import engine
from app.models import TransactionStream

with Session(engine) as s:
    sim_count = s.exec(
        select(TransactionStream).where(TransactionStream.tx_id.like('simlive_%'))
    ).all()
    sim_count = len(sim_count)
print(f'\n  [{"✓" if sim_count == 0 else "✗"}] simlive_ txns in DB: {sim_count} (must be 0)')
if sim_count > 0:
    all_ok = False

print('\n' + '=' * 60)
print('RESULT:', 'ALL PASS ✓' if all_ok else 'FAILURES ✗')
sys.exit(0 if all_ok else 1)