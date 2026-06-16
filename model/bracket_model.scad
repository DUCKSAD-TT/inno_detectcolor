
// Approximate 3D model from provided drawing/photos
// Units: mm
$fn=64;

th = 2.0;
base_w = 37.5;
base_l = 58.0;
side_h = 27.0;
inner_gap = 28.5;

module hole(x,y,d){
    translate([x,y,-1]) cylinder(h=th+2,d=d);
}

difference(){
    union(){
        // base
        cube([base_w,base_l,th]);

        // rear plate
        translate([0,0,th])
            cube([base_w,th,side_h]);

        // side tabs
        tab_w = (base_w-inner_gap)/2;
        translate([0,base_l-12,th])
            cube([tab_w,12,side_h]);
        translate([base_w-tab_w,base_l-12,th])
            cube([tab_w,12,side_h]);
    }

    // large holes on base (approx)
    hole(base_w/2, 20, 14);
    hole(base_w/2-8, 30, 14);
    hole(base_w/2+8, 30, 14);

    // small pattern holes (approx)
    for (x=[10,18.75,27.5])
      for (y=[12,24,36,48])
        hole(x,y,3);

    // rear plate holes (approx)
    for (x=[9,18.75,28.5])
      for (z=[8,16,24])
        translate([x,-1,th+z]) rotate([-90,0,0]) cylinder(h=th+2,d=3);

    // side tab holes
    tab_w = (base_w-inner_gap)/2;
    for (z=[10,20]) {
      translate([tab_w/2,base_l-6,th+z]) rotate([90,0,0]) cylinder(h=14,d=4);
      translate([base_w-tab_w/2,base_l-6,th+z]) rotate([90,0,0]) cylinder(h=14,d=4);
    }
}
