import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import glob
import os

def plot_deflection(file_path):
    # Read the CSV file into a DataFrame
    df = pd.read_csv(file_path)

    # Extract time and deflection data
    time = df.iloc[:, 0]
    deflection = np.arctan(df.iloc[:, 2] / df.iloc[:, 1])

    # Create a plot of deflection over time
    plt.figure(figsize=(10, 3))
    plt.plot(time, deflection, label='Deflection', color='blue')
    plt.title('Deflection Over Time')
    plt.xlabel('Time (s)')
    plt.ylabel('Deflection (rad)')
    # Save svg in same directory as the file path
    plt.savefig(file_path.replace('.csv', '.svg'))

def main():
    folder_path = 'motion-tracking/user-data/data'
    csv_files = glob.glob(os.path.join(folder_path, "*.csv"))
    for file_path in csv_files:
        plot_deflection(file_path)

if __name__ == "__main__":
    main()