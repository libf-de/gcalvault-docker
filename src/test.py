import settings

test = settings.Settings("peter", "", "", None, None, None, False, None)

jason = test.to_json()
print(jason)

test2 = settings.Settings.from_json(jason)

print(test2.username)