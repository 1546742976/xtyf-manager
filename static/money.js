// Amounts arrive as canonical decimal strings; money never passes through Number.
window.Money = {
  cents: function(value) {
    var text = String(value == null ? '0.00' : value);
    if (!/^-?\d+\.\d{2}$/.test(text)) throw new Error('金额格式无效');
    return BigInt(text.replace('.', ''));
  },
  format: function(value) {
    var text = String(value == null ? '0.00' : value);
    if (!/^-?\d+\.\d{2}$/.test(text)) return '—';
    var parts = text.split('.');
    return parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + parts[1];
  },
  compare: function(left, right) {
    var a = Money.cents(left), b = Money.cents(right);
    return a === b ? 0 : a > b ? 1 : -1;
  },
  max: function(values) {
    return values.reduce(function(max, value) { return Money.compare(value, max) > 0 ? value : max; }, '0.00');
  },
  percent: function(value, total) {
    var denominator = Money.cents(total);
    // Only the bounded chart width becomes a Number, after exact integer division.
    return denominator > 0n ? Number(Money.cents(value) * 1000n / denominator) / 10 : 0;
  }
};
