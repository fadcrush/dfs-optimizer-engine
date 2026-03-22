import Link from 'next/link'

export const metadata = {
  title: 'Privacy Policy — DFS Edge Pro',
  description: 'How DFS Edge Pro collects, uses, and protects your data.',
}

const EFFECTIVE_DATE = 'March 17, 2026'

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-surface-base px-6 py-12">
      <div className="mx-auto max-w-3xl prose prose-invert prose-sm">
        <h1 className="text-2xl font-extrabold text-text-primary mb-1">Privacy Policy</h1>
        <p className="text-xs text-text-muted mb-8">Effective date: {EFFECTIVE_DATE}</p>

        <Section title="1. Who We Are">
          <p>DFS Edge Pro (&ldquo;we&rdquo;, &ldquo;us&rdquo;, or &ldquo;our&rdquo;) is a professional daily fantasy sports analytics platform. Our services help you analyze player projections, optimize lineups, and manage contest data. We are the data controller for information collected through this platform.</p>
        </Section>

        <Section title="2. What Data We Collect">
          <ul>
            <li><strong>Account data:</strong> email address, full name, hashed password, subscription tier and status.</li>
            <li><strong>Usage data:</strong> slates you upload, projections you generate, lineups you optimize, and optimizer settings.</li>
            <li><strong>Transaction data:</strong> Stripe customer ID, subscription ID, and billing events. We do <em>not</em> store card numbers — Stripe handles all payment card data.</li>
            <li><strong>Technical data:</strong> server logs, IP address, browser type, and session timestamps for security and debugging.</li>
          </ul>
        </Section>

        <Section title="3. How We Use Your Data">
          <ul>
            <li>To provide and improve the DFS Edge Pro service.</li>
            <li>To authenticate your session and protect your account.</li>
            <li>To process your subscription and communicate billing updates.</li>
            <li>To send transactional emails (welcome, password reset). We do not send marketing emails without your explicit consent.</li>
            <li>To diagnose errors and monitor service reliability (Sentry error tracking).</li>
          </ul>
        </Section>

        <Section title="4. Data Sharing">
          <p>We share data only with:</p>
          <ul>
            <li><strong>Stripe</strong> — payment processing. See <a href="https://stripe.com/privacy" className="underline text-primary/80" target="_blank" rel="noopener noreferrer">stripe.com/privacy</a>.</li>
            <li><strong>Sentry</strong> — error monitoring. Sentry receives stack traces and may receive user IDs for error attribution.</li>
            <li><strong>Law enforcement</strong> — only when required by applicable law or court order.</li>
          </ul>
          <p>We do not sell your personal data to third parties.</p>
        </Section>

        <Section title="5. Data Retention">
          <p>We retain your account data for as long as your account is active. Contest results, slates, and optimizer outputs are retained indefinitely unless you request deletion. Server logs are pruned after 90 days.</p>
        </Section>

        <Section title="6. Your Rights">
          <p>Depending on your jurisdiction (GDPR, CCPA), you may have the right to:</p>
          <ul>
            <li><strong>Access</strong> the personal data we hold about you.</li>
            <li><strong>Correct</strong> inaccurate data.</li>
            <li><strong>Delete</strong> your account and all associated data (use <em>Settings → Delete Account</em> or email us).</li>
            <li><strong>Port</strong> your data in a machine-readable format.</li>
            <li><strong>Opt out</strong> of any future marketing communications.</li>
          </ul>
          <p>To exercise any of these rights, email <strong>privacy@dfsedge.com</strong>.</p>
        </Section>

        <Section title="7. Security">
          <p>Passwords are hashed with Argon2 and never stored in plaintext. All API traffic is served over HTTPS. Database files are backed up nightly. However, no method of transmission over the Internet is 100% secure.</p>
        </Section>

        <Section title="8. Cookies">
          <p>We use <code>localStorage</code> to store your authentication token client-side. We do not use tracking cookies or third-party advertising cookies.</p>
        </Section>

        <Section title="9. Children">
          <p>DFS Edge Pro is intended for users 18 years of age or older. We do not knowingly collect data from children under 18.</p>
        </Section>

        <Section title="10. Changes to This Policy">
          <p>We may update this policy. Continued use of the service after changes constitutes acceptance of the new policy. Significant changes will be communicated by email.</p>
        </Section>

        <Section title="11. Contact">
          <p>Questions? Email <strong>privacy@dfsedge.com</strong>.</p>
        </Section>

        <p className="mt-8 text-xs text-text-muted">
          <Link href="/terms" className="underline hover:text-text-secondary">Terms of Service</Link>
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
