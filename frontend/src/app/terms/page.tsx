import Link from 'next/link'

export const metadata = {
  title: 'Terms of Service — DFS Edge Pro',
  description: 'Terms governing your use of DFS Edge Pro.',
}

const EFFECTIVE_DATE = 'March 17, 2026'

export default function TermsPage() {
  return (
    <div className="min-h-screen bg-surface-base px-6 py-12">
      <div className="mx-auto max-w-3xl prose prose-invert prose-sm">
        <h1 className="text-2xl font-extrabold text-text-primary mb-1">Terms of Service</h1>
        <p className="text-xs text-text-muted mb-8">Effective date: {EFFECTIVE_DATE}</p>

        <Section title="1. Acceptance of Terms">
          <p>By creating an account or using DFS Edge Pro (&ldquo;Service&rdquo;), you agree to these Terms of Service (&ldquo;Terms&rdquo;). If you do not agree, do not use the Service.</p>
        </Section>

        <Section title="2. Description of Service">
          <p>DFS Edge Pro provides player projection generation, lineup optimization, expected value modeling, and contest analytics tools for daily fantasy sports play on third-party platforms including DraftKings and FanDuel. The Service is intended to assist users in making informed lineup decisions. It does not guarantee any particular contest result or return on investment.</p>
        </Section>

        <Section title="3. Eligibility">
          <ul>
            <li>You must be at least <strong>18 years old</strong> (or the legal age of majority in your jurisdiction, whichever is higher) to use the Service.</li>
            <li>You are responsible for verifying that daily fantasy sports contests are legal in your jurisdiction. DFS Edge Pro makes no representation regarding legality and accepts no liability for your participation in contests.</li>
            <li>The Service is not available in jurisdictions where DFS is prohibited by law.</li>
          </ul>
        </Section>

        <Section title="4. Account Registration">
          <ul>
            <li>You must provide accurate and complete information when creating your account.</li>
            <li>You are responsible for maintaining the confidentiality of your password and for all activity under your account.</li>
            <li>Notify us immediately of any unauthorized use of your account.</li>
          </ul>
        </Section>

        <Section title="5. Subscriptions and Billing">
          <ul>
            <li>Paid features require an active Pro or Elite subscription billed monthly or annually.</li>
            <li>Payments are processed by Stripe. By subscribing you authorize us to charge your payment method on a recurring basis.</li>
            <li>Subscriptions automatically renew unless cancelled before the renewal date.</li>
            <li>Refunds are issued at our discretion within 7 days of a charge for first-time subscribers. Contact <strong>billing@dfsedge.com</strong>.</li>
          </ul>
        </Section>

        <Section title="6. Acceptable Use">
          <p>You agree not to:</p>
          <ul>
            <li>Use the Service for any unlawful purpose or in violation of any applicable law.</li>
            <li>Reverse engineer, scrape, or attempt to extract the Service&apos;s algorithms, models, or data in bulk.</li>
            <li>Share your account credentials with others or resell access.</li>
            <li>Upload malicious files or attempt to compromise the security of the platform.</li>
            <li>Use the Service to facilitate cheating, multi-accounting, or collusion on DFS platforms.</li>
          </ul>
        </Section>

        <Section title="7. Disclaimer of Warranties">
          <p>THE SERVICE IS PROVIDED &ldquo;AS IS&rdquo; WITHOUT WARRANTIES OF ANY KIND. DFS EDGE PRO DOES NOT WARRANT THAT PROJECTIONS, OWNERSHIP ESTIMATES, OR OPTIMIZER OUTPUTS ARE ACCURATE, COMPLETE, OR PROFITABLE. DAILY FANTASY SPORTS INVOLVE RISK AND PAST PERFORMANCE DOES NOT GUARANTEE FUTURE RESULTS.</p>
        </Section>

        <Section title="8. Limitation of Liability">
          <p>TO THE MAXIMUM EXTENT PERMITTED BY LAW, DFS EDGE PRO AND ITS AFFILIATES SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, INCLUDING CONTEST LOSSES OR LOST PROFITS, ARISING FROM YOUR USE OF THE SERVICE.</p>
        </Section>

        <Section title="9. Intellectual Property">
          <p>The Service, including all algorithms, models, designs, and content, is owned by DFS Edge Pro and protected by copyright. You are granted a limited, non-exclusive, non-transferable license to use the Service for personal, non-commercial DFS purposes.</p>
        </Section>

        <Section title="10. Termination">
          <p>We may suspend or terminate your account at any time for violation of these Terms. You may delete your account at any time via <em>Settings → Delete Account</em>. Upon deletion, your personal data will be permanently erased per our Privacy Policy.</p>
        </Section>

        <Section title="11. Governing Law">
          <p>These Terms are governed by the laws of the State of Delaware, USA, without regard to conflict-of-law principles. Any disputes shall be resolved by binding arbitration under JAMS rules in Delaware.</p>
        </Section>

        <Section title="12. Changes to Terms">
          <p>We may update these Terms. Continued use of the Service after changes constitutes acceptance. We will email registered users about material changes at least 30 days in advance.</p>
        </Section>

        <Section title="13. Contact">
          <p>Questions about these Terms? Email <strong>legal@dfsedge.com</strong>.</p>
        </Section>

        <p className="mt-8 text-xs text-text-muted">
          <Link href="/privacy" className="underline hover:text-text-secondary">Privacy Policy</Link>
          {' · '}
          <Link href="/" className="underline hover:text-text-secondary">Home</Link>
        </p>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="text-base font-bold text-text-primary mb-3">{title}</h2>
      <div className="text-sm text-text-secondary leading-7 space-y-2">{children}</div>
    </section>
  )
}
