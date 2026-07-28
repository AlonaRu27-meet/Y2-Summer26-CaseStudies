def multiline_input():
    print("Paste text. Press Enter twice to finish.")

    lines = []

    while True:
        line = input()
        if line == "":
            break
        lines.append(line)

    return "\n".join(lines)

text = multiline_input()

print("-----")
print(text)
print("Length:", len(text))