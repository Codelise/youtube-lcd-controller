import textwrap;

def greet(name, times=1): # times has a default value
    for i in range(times):
        print(f"Hello {name} # {i}")

greet("Ana");
greet("Ben", 3);

lines = textwrap.wrap("We are the champions, my friends niggaaaaaaaaaa", 16) # wraps text at 16 characters per line
print(lines)# a list of strings
print(len(lines), lines[0]);

song = {"title": "Queen", "start": 1.5} #a dictionary
print(song["title"]);
