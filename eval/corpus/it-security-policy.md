# IT Security Policy

Security is everyone's job. This policy applies to every employee, contractor and device that accesses Halcyon Robotics systems.

## Passwords

Passwords must be at least 14 characters long and unique to each system. Store all work passwords in the company 1Password vault. Passwords do not need to be rotated on a schedule, but must be changed immediately if you suspect they were exposed.

## Multi-factor authentication

Multi-factor authentication is required on every company system. Hardware security keys (YubiKey) are required for anyone with production access or administrator rights. SMS codes are not an accepted second factor.

## Laptops

Company laptops must have full-disk encryption enabled and lock automatically after 5 minutes of inactivity. Operating system updates must be installed within 14 days of release.

If your laptop is lost or stolen, report it to the security team within 1 hour, in the #security-incidents Slack channel or by email to security@halcyon.example, so the device can be wiped remotely.

## Personal devices

Personal phones and tablets may access email and Slack only after they are enrolled in mobile device management (MDM). Source code must never be stored on a personal device.

## Data classification

All company data falls into one of four classes. Handle it according to its class.

| Class | Examples | Handling |
|---|---|---|
| Public | Marketing site, published docs | No restrictions |
| Internal | Org charts, internal wiki, this policy | Employees only, do not share externally |
| Confidential | Contracts, roadmap, financial forecasts | Need-to-know access, share via approved tools only |
| Restricted | Customer personal data, credentials, payroll | Approved systems only, never in email or Slack |

## Phishing

Report suspicious emails with the Report Phishing button in your mail client. The security team runs phishing simulations every quarter.

## Access reviews and offboarding

Managers review their team's system access every quarter. When someone leaves the company, all of their access is revoked within 4 hours of their termination.

## AI tools

Use only AI tools on the approved list, including the internal knowledge assistant. Never paste Confidential or Restricted data into an AI tool that is not on the approved list.
