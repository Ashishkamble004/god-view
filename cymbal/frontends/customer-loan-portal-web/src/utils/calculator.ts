export interface CalculatorInput {
  exShowroomInr: number;
  downPaymentInr: number;
  tenureMonths: number;
  interestRatePct: number;
  monthlyIncomeInr: number;
  existingObligationsInr: number;
  minDownPaymentPct: number;
}

export interface AmortizationYearRow {
  year: number;
  openingBalanceInr: number;
  principalPaidInr: number;
  interestPaidInr: number;
  closingBalanceInr: number;
}

export interface FinanceMetrics {
  exShowroomInr: number;
  downPaymentInr: number;
  principalInr: number;
  emiInr: number;
  totalPayableInr: number;
  totalInterestInr: number;
  ltvPct: number;
  downPaymentPct: number;
  foirPct: number;
  foirEligible: boolean;
  ltvEligible: boolean;
  downPaymentEligible: boolean;
  schedule: AmortizationYearRow[];
}

const inrFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});

export function formatInr(amount: number): string {
  if (!Number.isFinite(amount)) return '₹0';
  return inrFormatter.format(Math.round(amount));
}

export function validatePanNumber(pan: string): boolean {
  return /^[A-Z]{5}[0-9]{4}[A-Z]{1}$/.test(pan);
}

export function validateAadhaarNumber(aadhaar: string): boolean {
  return /^[0-9]{12}$/.test(aadhaar);
}

export function maskPan(pan?: string | null): string {
  if (!pan) return '—';
  const cleaned = pan.trim().toUpperCase();
  if (cleaned.length < 10) return '••••••••••';
  return `${cleaned.slice(0, 3)}••••${cleaned.slice(7, 10)}`;
}

export function calculateFinanceMetrics(input: CalculatorInput): FinanceMetrics {
  const exShowroomInr = Math.max(1, Math.round(input.exShowroomInr));
  const downPaymentInr = Math.min(
    exShowroomInr,
    Math.max(0, Math.round(input.downPaymentInr))
  );
  const principalInr = Math.max(0, exShowroomInr - downPaymentInr);
  const tenureMonths = Math.max(6, Math.round(input.tenureMonths));
  const annualRate = Math.max(0, input.interestRatePct);
  const monthlyRate = annualRate / (12 * 100);

  let emiInr = 0;
  if (principalInr > 0) {
    if (monthlyRate === 0) {
      emiInr = Math.round(principalInr / tenureMonths);
    } else {
      const factor = Math.pow(1 + monthlyRate, tenureMonths);
      emiInr = Math.round((principalInr * monthlyRate * factor) / (factor - 1));
    }
  }

  const totalPayableInr = emiInr * tenureMonths;
  const totalInterestInr = Math.max(0, totalPayableInr - principalInr);
  const ltvPct = Number(((principalInr / exShowroomInr) * 100).toFixed(2));
  const downPaymentPct = Number(
    ((downPaymentInr / exShowroomInr) * 100).toFixed(2)
  );

  const monthlyIncome = Math.max(1, input.monthlyIncomeInr);
  const existingObligations = Math.max(0, input.existingObligationsInr);
  const foirPct = Number(
    (((existingObligations + emiInr) / monthlyIncome) * 100).toFixed(2)
  );

  const foirEligible = foirPct <= 60;
  const ltvEligible = ltvPct <= 90;
  const downPaymentEligible = downPaymentPct + 0.01 >= input.minDownPaymentPct;

  // Build annual amortization summary
  const schedule: AmortizationYearRow[] = [];
  let balance = principalInr;
  const totalYears = Math.ceil(tenureMonths / 12);

  for (let year = 1; year <= totalYears; year++) {
    const openingBalanceInr = Math.round(balance);
    let yearPrincipal = 0;
    let yearInterest = 0;
    const monthsInThisYear = Math.min(12, tenureMonths - (year - 1) * 12);

    for (let m = 0; m < monthsInThisYear; m++) {
      if (balance <= 0) break;
      const monthInterest = balance * monthlyRate;
      const monthPrincipal = Math.min(balance, Math.max(0, emiInr - monthInterest));
      yearInterest += monthInterest;
      yearPrincipal += monthPrincipal;
      balance = Math.max(0, balance - monthPrincipal);
    }

    schedule.push({
      year,
      openingBalanceInr,
      principalPaidInr: Math.round(yearPrincipal),
      interestPaidInr: Math.round(yearInterest),
      closingBalanceInr: Math.round(balance),
    });
  }

  return {
    exShowroomInr,
    downPaymentInr,
    principalInr,
    emiInr,
    totalPayableInr,
    totalInterestInr,
    ltvPct,
    downPaymentPct,
    foirPct,
    foirEligible,
    ltvEligible,
    downPaymentEligible,
    schedule,
  };
}
