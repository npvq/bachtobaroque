from django.shortcuts import render

# Create your views here.
def landing(request):
	return render(request, 'fourpart/index.html')

def testing(request):
	return render(request, 'base.html')