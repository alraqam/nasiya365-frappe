/**
 * Installment Calculator
 * Provides a quick calculator accessible from navbar for computing monthly installment payments
 */

frappe.provide('nasiya365');

nasiya365.InstallmentCalculator = class {
    constructor() {
        this.dialog = null;
        this.setup_navbar_icon();
    }

    setup_navbar_icon() {
        // Add calculator icon to navbar
        $(document).on('toolbar_setup', () => {
            const navbar = $('.navbar-right');
            if (navbar.length && !$('#installment-calculator-icon').length) {
                const icon = $(`
                    <li id="installment-calculator-icon">
                        <a href="#" title="${__('Installment Calculator')}">
                            <i class="fa fa-calculator"></i>
                        </a>
                    </li>
                `);

                icon.on('click', (e) => {
                    e.preventDefault();
                    this.show_calculator();
                });

                navbar.prepend(icon);
            }
        });
    }

    show_calculator() {
        if (!this.dialog) {
            this.dialog = new frappe.ui.Dialog({
                title: __('Installment Calculator'),
                fields: [
                    {
                        fieldname: 'product_price',
                        fieldtype: 'Currency',
                        label: __('Product Price'),
                        reqd: 1,
                        default: 0,
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'down_payment_percent',
                        fieldtype: 'Percent',
                        label: __('Down Payment %'),
                        default: 20,
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Section Break'
                    },
                    {
                        fieldname: 'installment_months',
                        fieldtype: 'Select',
                        label: __('Installment Period'),
                        options: ['3', '6', '9', '12', '18', '24'],
                        default: '12',
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'interest_rate',
                        fieldtype: 'Percent',
                        label: __('Annual Interest Rate %'),
                        default: 24,
                        description: __('Default rate from Merchant Settings'),
                        onchange: () => this.calculate()
                    },
                    {
                        fieldtype: 'Section Break',
                        label: __('Results')
                    },
                    {
                        fieldname: 'down_payment_amount',
                        fieldtype: 'Currency',
                        label: __('Down Payment Amount'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'financed_amount',
                        fieldtype: 'Currency',
                        label: __('Financed Amount'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Section Break'
                    },
                    {
                        fieldname: 'monthly_payment',
                        fieldtype: 'Currency',
                        label: __('Monthly Payment'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Column Break'
                    },
                    {
                        fieldname: 'total_interest',
                        fieldtype: 'Currency',
                        label: __('Total Interest'),
                        read_only: 1
                    },
                    {
                        fieldtype: 'Section Break'
                    },
                    {
                        fieldname: 'total_payment',
                        fieldtype: 'Currency',
                        label: __('Total Payment (Principal + Interest)'),
                        read_only: 1
                    }
                ],
                primary_action_label: __('Close'),
                primary_action: () => {
                    this.dialog.hide();
                }
            });
        }

        this.dialog.show();
        this.calculate();
    }

    calculate() {
        if (!this.dialog) return;

        const values = this.dialog.get_values();
        if (!values || !values.product_price) return;

        const price = parseFloat(values.product_price) || 0;
        const down_percent = parseFloat(values.down_payment_percent) || 0;
        const months = parseInt(values.installment_months) || 12;
        const annual_rate = parseFloat(values.interest_rate) || 0;

        // Calculate down payment
        const down_payment = price * (down_percent / 100);
        const principal = price - down_payment;

        // Calculate monthly payment using amortization formula
        const monthly_rate = annual_rate / 12 / 100;
        let monthly_payment = 0;
        let total_interest = 0;

        if (monthly_rate > 0) {
            monthly_payment = (principal * monthly_rate) / (1 - Math.pow(1 + monthly_rate, -months));
            total_interest = (monthly_payment * months) - principal;
        } else {
            // No interest
            monthly_payment = principal / months;
            total_interest = 0;
        }

        const total_payment = down_payment + (monthly_payment * months);

        // Update result fields
        this.dialog.set_value('down_payment_amount', down_payment.toFixed(2));
        this.dialog.set_value('financed_amount', principal.toFixed(2));
        this.dialog.set_value('monthly_payment', monthly_payment.toFixed(2));
        this.dialog.set_value('total_interest', total_interest.toFixed(2));
        this.dialog.set_value('total_payment', total_payment.toFixed(2));
    }
};

// Initialize calculator when page loads
$(document).ready(() => {
    nasiya365.calculator = new nasiya365.InstallmentCalculator();
});
