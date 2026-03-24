import csv

path = r"F:\Dev\N_B_A_and_N_F_L\data\player_game_logs_2025-26.csv"
with open(path, encoding="utf-8") as f:
    reader = csv.reader(f)
    header = next(reader)
    row1 = next(reader)
    row2 = next(reader)

print("HEADERS:", header)
print()
for col, v1, v2 in zip(header, row1, row2):
    print(f"  {col!r:40s} | {v1!r:25s} | {v2!r}")
