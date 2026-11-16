// Thermal receipts print on a roll, so the page should be exactly as long as
// the receipt: a fixed height would feed blank paper after it. CSS can't size a
// page to its content, so measure the [data-receipt] element just before
// printing and set the height of the named @page the receipt prints on.
(function () {
  const PAGES = { '58mm': 'thermal58', '80mm': 'thermal80' };
  const FEED_MM = 6;

  function fitPageToReceipt() {
    const style = document.getElementById('thermal-page-size') || document.head.appendChild(
      Object.assign(document.createElement('style'), { id: 'thermal-page-size' })
    );
    const paper = document.body.dataset.paperSize;
    const receipt = document.querySelector('[data-receipt]');
    if (!PAGES[paper] || !receipt) {
      style.textContent = '';
      return;
    }
    const heightMm = Math.ceil(receipt.offsetHeight * 25.4 / 96) + FEED_MM;
    style.textContent = '@page ' + PAGES[paper] + ' { size: ' + paper + ' ' + heightMm + 'mm; margin: 0; }';
  }

  window.addEventListener('beforeprint', fitPageToReceipt);
})();

// The paper-size dropdown: each computer remembers the paper it last printed
// on, so a desk with an A4 printer doesn't have to pick A4 for every receipt.
(function () {
  const KEY = 'ternah.receiptPaperSize';
  const SIZES = ['58mm', '80mm', 'a4'];

  function setUp() {
    const select = document.querySelector('[data-paper-select]');
    if (!select) return;
    let saved = null;
    try { saved = window.localStorage.getItem(KEY); } catch (e) { /* storage blocked */ }
    if (SIZES.indexOf(saved) !== -1) select.value = saved;
    document.body.setAttribute('data-paper-size', select.value);
    select.addEventListener('change', function () {
      document.body.setAttribute('data-paper-size', select.value);
      try { window.localStorage.setItem(KEY, select.value); } catch (e) { /* storage blocked */ }
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setUp);
  else setUp();
})();
