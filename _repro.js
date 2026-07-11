// Reproduce the dashboard JS logic to find the "Cannot read properties of undefined (reading 'bkash')" error
const fs = require('fs');

// Load dashboard.js and extract the DASH.LiveWallet block
const src = fs.readFileSync('app/templates/static/dashboard.js', 'utf-8');
const window = {};
eval(src.replace('window.DASH =', 'window.DASH ='));  // run as-is
const DASH = window.DASH;

// Replay the agent-dash.html flow with realistic data
const JSON_PROFILE = {
  profile: {
    agent_id: 'agent1000',
    shop_name: 'Prime Cash Express',
    shared_physical_cash: 450000,
    provider_wallets: [
      { provider_id: 'bkash',  e_money_balance: 38000 },
      { provider_id: 'nagad',  e_money_balance: 25000 },
      { provider_id: 'rocket', e_money_balance: 18000 },
    ],
  },
};

const liveCash = DASH.LiveCash.create({ starting_cash: JSON_PROFILE.profile.shared_physical_cash });
const liveWallet = DASH.LiveWallet.create(JSON_PROFILE.profile.provider_wallets);

// Realistic txns (from API shape: provider_id is '1'/'2'/'3')
const txns = [
  { tx_id: 'live_1', provider_id: '1', type: 'CASH_OUT', amount: 5000 },
  { tx_id: 'live_2', provider_id: '2', type: 'CASH_IN',  amount: 3000 },
  { tx_id: 'live_3', provider_id: '3', type: 'CASH_OUT', amount: 2000 },
  { tx_id: 'live_4', provider_id: '1', type: 'CASH_OUT', amount: 1500 },
];

liveCash.applyBatch(txns);
liveWallet.applyBatch(txns);

const snap = liveCash.snapshot();
const walletSnap = liveWallet.snapshotAll();

console.log('Physical:', snap);
console.log('Wallet snapshot per:', walletSnap.per);
console.log('Total:', walletSnap.total);

// Now exercise the render() pattern
try {
  const balanceCards = Object.entries(walletSnap.per).map(([pkey, w]) => {
    return `<div class="balance-card ${pkey}">${w.provider_name}: ${w.balance}</div>`;
  }).join('');
  console.log('\nRendered cards OK:');
  console.log(balanceCards);
} catch (e) {
  console.error('\n[ERROR]', e.message);
  console.error(e.stack);
}

// Now simulate the BROKEN case: profile without provider_wallets (stale sessionStorage)
console.log('\n\n=== BROKEN CASE: JSON_PROFILE without provider_wallets ===');
const brokenProfile = { profile: { agent_id: 'agent1000', shared_physical_cash: 450000 } };
const liveWallet2 = DASH.LiveWallet.create(brokenProfile.profile.provider_wallets || []);
try {
  const snap2 = liveWallet2.snapshotAll();
  console.log('Snapshot:', snap2);
  console.log('Object.entries:', Object.entries(snap2.per));
} catch (e) {
  console.error('[ERROR]', e.message);
}