import os
import joblib
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier

def train_and_save_model():
    # 1. Generate synthetic data (mimicking Telco Customer Churn dataset structure)
    np.random.seed(42)
    n_samples = 1000

    tenure = np.random.randint(1, 72, n_samples)
    monthly_charges = np.random.uniform(18.0, 118.0, n_samples)
    total_charges = tenure * monthly_charges + np.random.normal(0, 50, n_samples)
    total_charges = np.maximum(total_charges, monthly_charges)

    # Higher monthly charges and lower tenure increase churn probability
    churn_prob = 1 / (1 + np.exp(-(-0.05 * tenure + 0.03 * monthly_charges - 1)))
    churn = (np.random.rand(n_samples) < churn_prob).astype(int)

    df = pd.DataFrame({
        'tenure': tenure,
        'MonthlyCharges': monthly_charges,
        'TotalCharges': total_charges,
        'Churn': churn
    })

    # 2. Separate Features and Target
    X = df[['tenure', 'MonthlyCharges', 'TotalCharges']]
    y = df['Churn']

    # 3. Split Dataset
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # 4. Scale Features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    # 5. Train Model
    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_train_scaled, y_train)

    # 6. Save Model and Scaler to the model/ directory
    model_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(model_dir, 'churn_model.pkl')
    scaler_path = os.path.join(model_dir, 'scaler.pkl')

    joblib.dump(model, model_path)
    joblib.dump(scaler, scaler_path)

    print(f"Model successfully saved to: {model_path}")
    print(f"Scaler successfully saved to: {scaler_path}")

if __name__ == '__main__':
    train_and_save_model()