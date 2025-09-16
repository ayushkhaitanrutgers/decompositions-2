from series_summation import series_to_bound
from mathematica_export import question

# Define your example series objects here. Add more as needed.
series_1 = series_to_bound(
    formula="(2*d+1)/(2*h^2*(1+d*(d+1)/(h^2))(1+d*(d+1)/(h^2*m^2))^2)",
    conditions="h >1 && m > 1",
    summation_index="d",
    other_variables="{h,m}",
    summation_bounds=["0", "Infinity"],
    conjectured_upper_asymptotic_bound="1+Log[m^2]",
)



question_1 = question(
    variables="{x,y}",
    domain_description="{x>0, y>1}",
    lhs="x*y",
    rhs="y*Log[y]+Exp[x]",
)

question_2 = question(
    variables = "x,y,z", 
    domain_description = "x>0, y>0, z>0", 
    lhs = "(x*y*z)^(1/3)", 
    rhs = "(x+y+z)/3"
    )

question_3 = question(
    variables = "x,y,z", 
    domain_description = "x>0, y>0", 
    lhs = "(x*y)^(1/2)", 
    rhs = "(x+y)/2"
    )
