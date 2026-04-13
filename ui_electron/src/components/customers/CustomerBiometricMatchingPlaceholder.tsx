import { Card } from '..';
import './CustomerIdentity.css';

export function CustomerBiometricMatchingPlaceholder() {
    return (
        <div className="customer-identity-card-compact">
            <Card title="Biometric Matching">
                <div className="customer-identity-empty">
                    Placeholder for future biometric matching implementation and integration.
                </div>
            </Card>
        </div>
    );
}
