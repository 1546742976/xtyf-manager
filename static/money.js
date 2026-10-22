// Amounts arrive as canonical decimal strings; money never passes through Number.
window.Money = {
  inputCents: function(value) {
    var text = String(value == null ? '' : value).trim();
    var match = /^[+]?((?:\d+(?:\.\d*)?|\.\d+))(?:[eE]([+-]?\d+))?$/.exec(text);
    if (!match || text.length > 128) throw new Error('金额格式无效');
    var parts = match[1].split('.'), digits = BigInt(parts.join(''));
    var shift = Number(match[2] || 0) + 2 - (parts[1] || '').length;
    if (!digits) return 0n;
    if (shift > 200) throw new Error('金额超出范围');
    if (shift < -200) return 0n;
    var divisor = shift < 0 ? 10n ** BigInt(-shift) : 1n;
    var numerator = shift < 0 ? digits : digits * 10n ** BigInt(shift);
    if (numerator > 9223372036854775807n * divisor) throw new Error('金额超出范围');
    return (numerator * 2n + divisor) / (divisor * 2n);
  },
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
